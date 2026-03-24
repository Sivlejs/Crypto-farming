// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title  FlashArbitrage
 * @notice Atomic flash-loan arbitrage contract for Nexus AI.
 *
 * Supports:
 *   • Aave V3  flash loans  (0.09% fee)
 *   • Balancer flash loans  (0% fee)
 *
 * Flow (Aave):
 *   1. Owner calls executeAaveArbitrage()
 *   2. Contract borrows `amount` of `asset` from Aave (no collateral needed)
 *   3. executeOperation() callback fires:
 *       a. Swap asset → tokenB on DEX A (buy cheap)
 *       b. Swap tokenB → asset on DEX B (sell high)
 *       c. Repay loan + 0.09% fee to Aave
 *       d. Transfer profit to owner
 *   4. All steps are atomic — if profit < minProfit the tx reverts (no loss)
 *
 * Deploy this contract once per chain, then set FLASH_CONTRACT_ADDRESS in .env
 */

interface IPool {
    function flashLoanSimple(
        address receiverAddress,
        address asset,
        uint256 amount,
        bytes   calldata params,
        uint16  referralCode
    ) external;
}

interface IFlashLoanSimpleReceiver {
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes   calldata params
    ) external returns (bool);
}

interface IVault {
    function flashLoan(
        address recipient,
        address[] memory tokens,
        uint256[] memory amounts,
        bytes memory userData
    ) external;
}

interface IFlashLoanRecipient {
    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external;
}

interface IUniswapV2Router {
    function swapExactTokensForTokens(
        uint    amountIn,
        uint    amountOutMin,
        address[] calldata path,
        address to,
        uint    deadline
    ) external returns (uint[] memory amounts);
}

interface IERC20 {
    function approve(address spender, uint256 amount) external returns (bool);
    function transfer(address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
}

contract FlashArbitrage is IFlashLoanSimpleReceiver, IFlashLoanRecipient {

    address public owner;
    IPool   public aavePool;
    IVault  public balancerVault;

    event ArbitrageExecuted(
        address indexed asset,
        uint256 borrowed,
        uint256 profit,
        address buyDex,
        address sellDex
    );

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    constructor(address _aavePool, address _balancerVault) {
        owner         = msg.sender;
        aavePool      = IPool(_aavePool);
        balancerVault = IVault(_balancerVault);
    }

    // ── Aave entry point ──────────────────────────────────────

    /**
     * @notice Trigger an Aave V3 flash-loan arbitrage.
     * @param asset     Token to borrow (e.g. USDC)
     * @param amount    Amount to borrow (in token decimals)
     * @param routerA   DEX router to BUY on (cheaper price)
     * @param routerB   DEX router to SELL on (higher price)
     * @param pathAB    Swap path on routerA  (e.g. [USDC, WETH])
     * @param pathBA    Swap path on routerB  (e.g. [WETH, USDC])
     * @param minProfit Minimum profit required or tx reverts (safety guard)
     */
    function executeAaveArbitrage(
        address   asset,
        uint256   amount,
        address   routerA,
        address   routerB,
        address[] calldata pathAB,
        address[] calldata pathBA,
        uint256   minProfit
    ) external onlyOwner {
        bytes memory params = abi.encode(
            routerA, routerB, pathAB, pathBA, minProfit
        );
        aavePool.flashLoanSimple(address(this), asset, amount, params, 0);
    }

    /// @dev Aave calls this after sending `amount` to this contract
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata params
    ) external override returns (bool) {
        require(msg.sender == address(aavePool),  "Caller not Aave pool");
        require(initiator  == address(this),      "Initiator mismatch");

        (
            address routerA,
            address routerB,
            address[] memory pathAB,
            address[] memory pathBA,
            uint256 minProfit
        ) = abi.decode(params, (address, address, address[], address[], uint256));

        uint256 profit = _doSwaps(asset, amount, routerA, routerB, pathAB, pathBA, minProfit);

        // Repay Aave
        uint256 owed = amount + premium;
        IERC20(asset).approve(address(aavePool), owed);

        // Profit to owner
        if (profit > 0) {
            IERC20(asset).transfer(owner, profit);
        }

        emit ArbitrageExecuted(asset, amount, profit, routerA, routerB);
        return true;
    }

    // ── Balancer entry point ──────────────────────────────────

    /**
     * @notice Trigger a Balancer 0%-fee flash-loan arbitrage.
     */
    function executeBalancerArbitrage(
        address   asset,
        uint256   amount,
        address   routerA,
        address   routerB,
        address[] calldata pathAB,
        address[] calldata pathBA,
        uint256   minProfit
    ) external onlyOwner {
        address[] memory tokens  = new address[](1);
        uint256[] memory amounts = new uint256[](1);
        tokens[0]  = asset;
        amounts[0] = amount;

        bytes memory userData = abi.encode(
            routerA, routerB, pathAB, pathBA, minProfit
        );
        balancerVault.flashLoan(address(this), tokens, amounts, userData);
    }

    /// @dev Balancer calls this after sending tokens to this contract
    function receiveFlashLoan(
        address[] memory tokens,
        uint256[] memory amounts,
        uint256[] memory feeAmounts,
        bytes memory userData
    ) external override {
        require(msg.sender == address(balancerVault), "Caller not Balancer");

        address asset  = tokens[0];
        uint256 amount = amounts[0];
        uint256 fee    = feeAmounts[0]; // 0 for Balancer

        (
            address routerA,
            address routerB,
            address[] memory pathAB,
            address[] memory pathBA,
            uint256 minProfit
        ) = abi.decode(userData, (address, address, address[], address[], uint256));

        uint256 profit = _doSwaps(asset, amount, routerA, routerB, pathAB, pathBA, minProfit);

        // Repay Balancer (fee = 0)
        IERC20(asset).transfer(address(balancerVault), amount + fee);

        // Profit to owner
        if (profit > 0) {
            IERC20(asset).transfer(owner, profit);
        }

        emit ArbitrageExecuted(asset, amount, profit, routerA, routerB);
    }

    // ── Internal swap logic ───────────────────────────────────

    function _doSwaps(
        address   asset,
        uint256   amount,
        address   routerA,
        address   routerB,
        address[] memory pathAB,
        address[] memory pathBA,
        uint256   minProfit
    ) internal returns (uint256 profit) {
        // Swap 1: asset → tokenB on routerA
        IERC20(asset).approve(routerA, amount);
        uint256[] memory amountsAB = IUniswapV2Router(routerA)
            .swapExactTokensForTokens(
                amount, 0, pathAB, address(this), block.timestamp + 300
            );

        // Swap 2: tokenB → asset on routerB
        address tokenB = pathAB[pathAB.length - 1];
        uint256 amountB = amountsAB[amountsAB.length - 1];
        IERC20(tokenB).approve(routerB, amountB);
        uint256[] memory amountsBA = IUniswapV2Router(routerB)
            .swapExactTokensForTokens(
                amountB, 0, pathBA, address(this), block.timestamp + 300
            );

        uint256 received = amountsBA[amountsBA.length - 1];
        require(received > amount, "No profit after swaps");

        profit = received - amount;
        require(profit >= minProfit, "Profit below minimum threshold");
    }

    // ── Admin ─────────────────────────────────────────────────

    function withdrawToken(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(bal > 0, "Nothing to withdraw");
        IERC20(token).transfer(owner, bal);
    }

    function withdrawETH() external onlyOwner {
        payable(owner).transfer(address(this).balance);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        owner = newOwner;
    }

    receive() external payable {}
}
