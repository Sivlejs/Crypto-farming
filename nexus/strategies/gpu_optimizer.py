"""
Advanced GPU Mining Optimizer for Nexus AI.

Maximizes mining efficiency on virtual servers and cloud GPU instances to
compete with dedicated ASIC/FPGA hardware through:

- Automatic GPU detection and optimal parameter selection
- Per-GPU overclocking and power optimization (where supported)
- Memory timing optimization for mining workloads
- Algorithm-specific tuning profiles
- Cloud GPU instance optimization (AWS P3/P4, GCP A100/T4, Azure NC)
- Dynamic hashrate optimization with auto-tuning
- Thermal management and throttle prevention
- Multi-GPU load balancing and orchestration
- Real-time performance monitoring and adjustment

This module is designed to extract maximum performance from cloud GPU
instances while maintaining stability and preventing provider throttling.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from nexus.utils.logger import get_logger

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Cloud GPU Instance Detection and Profiles
# ══════════════════════════════════════════════════════════════════════════════

class CloudGPUType(str, Enum):
    """Known cloud GPU instance types with mining profiles."""
    # AWS GPU Instances
    AWS_P3_V100 = "aws_p3_v100"      # Tesla V100 16GB
    AWS_P3DN_V100 = "aws_p3dn_v100"  # Tesla V100 32GB
    AWS_P4D_A100 = "aws_p4d_a100"    # A100 40GB
    AWS_G4DN_T4 = "aws_g4dn_t4"      # Tesla T4
    AWS_G5_A10G = "aws_g5_a10g"      # A10G
    
    # GCP GPU Instances
    GCP_A100 = "gcp_a100"            # A100 40/80GB
    GCP_V100 = "gcp_v100"            # V100 16GB
    GCP_T4 = "gcp_t4"                # T4
    GCP_L4 = "gcp_l4"                # L4
    GCP_P100 = "gcp_p100"            # P100
    
    # Azure GPU Instances
    AZURE_NC_V100 = "azure_nc_v100"  # V100
    AZURE_NC_T4 = "azure_nc_t4"      # T4
    AZURE_ND_A100 = "azure_nd_a100"  # A100
    
    # Generic/Unknown
    GENERIC_NVIDIA = "generic_nvidia"
    GENERIC_AMD = "generic_amd"
    LOCAL_GPU = "local_gpu"


@dataclass
class GPUMiningProfile:
    """
    Optimized mining profile for a specific GPU type.
    
    Contains tuned parameters for maximum hashrate while maintaining
    stability and preventing thermal throttling.
    """
    gpu_type: CloudGPUType
    gpu_name: str
    
    # Core settings
    core_clock_offset: int = 0       # MHz offset from stock
    memory_clock_offset: int = 0     # MHz offset from stock
    power_limit_percent: int = 100   # % of TDP
    fan_speed_percent: int = 70      # Target fan speed %
    
    # Mining-specific settings
    intensity: int = 100             # Mining intensity (1-100)
    cuda_threads: int = 0            # CUDA threads per block (0=auto)
    worksize: int = 0                # OpenCL worksize (0=auto)
    
    # Algorithm-specific hashrates (H/s estimates)
    expected_hashrates: Dict[str, float] = field(default_factory=dict)
    
    # Thermal limits
    temp_limit_c: int = 83           # Hard temperature limit
    temp_target_c: int = 75          # Target temperature
    
    # Power efficiency
    expected_power_watts: int = 0    # Expected power draw
    efficiency_score: float = 0.0    # H/W efficiency rating
    
    def to_dict(self) -> dict:
        return {
            "gpu_type": self.gpu_type.value,
            "gpu_name": self.gpu_name,
            "core_clock_offset": self.core_clock_offset,
            "memory_clock_offset": self.memory_clock_offset,
            "power_limit_percent": self.power_limit_percent,
            "fan_speed_percent": self.fan_speed_percent,
            "intensity": self.intensity,
            "cuda_threads": self.cuda_threads,
            "worksize": self.worksize,
            "expected_hashrates": self.expected_hashrates,
            "temp_limit_c": self.temp_limit_c,
            "temp_target_c": self.temp_target_c,
            "expected_power_watts": self.expected_power_watts,
            "efficiency_score": self.efficiency_score,
        }


# Pre-defined mining profiles for known GPU types
# These are optimized for mining efficiency while maintaining stability
GPU_MINING_PROFILES: Dict[str, GPUMiningProfile] = {
    # ═══════════════════════════════════════════════════════════════
    # NVIDIA Data Center GPUs (Cloud Instances)
    # ═══════════════════════════════════════════════════════════════
    
    "Tesla V100": GPUMiningProfile(
        gpu_type=CloudGPUType.AWS_P3_V100,
        gpu_name="Tesla V100",
        core_clock_offset=100,
        memory_clock_offset=500,
        power_limit_percent=85,
        fan_speed_percent=80,
        intensity=100,
        cuda_threads=1024,
        expected_hashrates={
            "ethash": 95e6,      # 95 MH/s
            "etchash": 95e6,
            "kawpow": 28e6,      # 28 MH/s
            "autolykos2": 180e6, # 180 MH/s
            "kheavyhash": 550e6, # 550 MH/s (Kaspa)
        },
        temp_limit_c=83,
        temp_target_c=72,
        expected_power_watts=250,
        efficiency_score=0.38,  # MH/W for ethash
    ),
    
    "A100": GPUMiningProfile(
        gpu_type=CloudGPUType.AWS_P4D_A100,
        gpu_name="NVIDIA A100",
        core_clock_offset=150,
        memory_clock_offset=700,
        power_limit_percent=80,
        fan_speed_percent=75,
        intensity=100,
        cuda_threads=1024,
        expected_hashrates={
            "ethash": 125e6,     # 125 MH/s
            "etchash": 125e6,
            "kawpow": 42e6,      # 42 MH/s
            "autolykos2": 260e6, # 260 MH/s
            "kheavyhash": 800e6, # 800 MH/s
            "octopus": 58e6,     # 58 MH/s
        },
        temp_limit_c=80,
        temp_target_c=70,
        expected_power_watts=300,
        efficiency_score=0.42,
    ),
    
    "Tesla T4": GPUMiningProfile(
        gpu_type=CloudGPUType.AWS_G4DN_T4,
        gpu_name="Tesla T4",
        core_clock_offset=100,
        memory_clock_offset=400,
        power_limit_percent=90,
        fan_speed_percent=70,
        intensity=95,
        cuda_threads=512,
        expected_hashrates={
            "ethash": 28e6,      # 28 MH/s
            "etchash": 28e6,
            "kawpow": 12e6,      # 12 MH/s
            "autolykos2": 65e6,  # 65 MH/s
            "kheavyhash": 200e6, # 200 MH/s
        },
        temp_limit_c=83,
        temp_target_c=75,
        expected_power_watts=70,
        efficiency_score=0.40,
    ),
    
    "A10G": GPUMiningProfile(
        gpu_type=CloudGPUType.AWS_G5_A10G,
        gpu_name="NVIDIA A10G",
        core_clock_offset=120,
        memory_clock_offset=500,
        power_limit_percent=85,
        fan_speed_percent=75,
        intensity=100,
        cuda_threads=768,
        expected_hashrates={
            "ethash": 65e6,      # 65 MH/s
            "etchash": 65e6,
            "kawpow": 24e6,      # 24 MH/s
            "autolykos2": 130e6, # 130 MH/s
            "kheavyhash": 400e6, # 400 MH/s
        },
        temp_limit_c=83,
        temp_target_c=73,
        expected_power_watts=150,
        efficiency_score=0.43,
    ),
    
    "L4": GPUMiningProfile(
        gpu_type=CloudGPUType.GCP_L4,
        gpu_name="NVIDIA L4",
        core_clock_offset=100,
        memory_clock_offset=400,
        power_limit_percent=90,
        fan_speed_percent=70,
        intensity=95,
        cuda_threads=512,
        expected_hashrates={
            "ethash": 35e6,      # 35 MH/s
            "etchash": 35e6,
            "kawpow": 15e6,      # 15 MH/s
            "autolykos2": 80e6,  # 80 MH/s
            "kheavyhash": 250e6, # 250 MH/s
        },
        temp_limit_c=83,
        temp_target_c=75,
        expected_power_watts=72,
        efficiency_score=0.49,
    ),
    
    "P100": GPUMiningProfile(
        gpu_type=CloudGPUType.GCP_P100,
        gpu_name="Tesla P100",
        core_clock_offset=50,
        memory_clock_offset=300,
        power_limit_percent=90,
        fan_speed_percent=75,
        intensity=95,
        cuda_threads=768,
        expected_hashrates={
            "ethash": 45e6,      # 45 MH/s
            "etchash": 45e6,
            "kawpow": 16e6,      # 16 MH/s
            "autolykos2": 95e6,  # 95 MH/s
        },
        temp_limit_c=85,
        temp_target_c=75,
        expected_power_watts=180,
        efficiency_score=0.25,
    ),
    
    # ═══════════════════════════════════════════════════════════════
    # NVIDIA Consumer GPUs (if running locally or in custom setup)
    # ═══════════════════════════════════════════════════════════════
    
    "RTX 4090": GPUMiningProfile(
        gpu_type=CloudGPUType.LOCAL_GPU,
        gpu_name="GeForce RTX 4090",
        core_clock_offset=150,
        memory_clock_offset=1000,
        power_limit_percent=75,
        fan_speed_percent=70,
        intensity=100,
        cuda_threads=1024,
        expected_hashrates={
            "ethash": 130e6,     # 130 MH/s
            "etchash": 130e6,
            "kawpow": 62e6,      # 62 MH/s
            "autolykos2": 280e6, # 280 MH/s
            "kheavyhash": 900e6, # 900 MH/s
            "octopus": 85e6,     # 85 MH/s
        },
        temp_limit_c=83,
        temp_target_c=70,
        expected_power_watts=320,
        efficiency_score=0.41,
    ),
    
    "RTX 4080": GPUMiningProfile(
        gpu_type=CloudGPUType.LOCAL_GPU,
        gpu_name="GeForce RTX 4080",
        core_clock_offset=150,
        memory_clock_offset=900,
        power_limit_percent=80,
        fan_speed_percent=70,
        intensity=100,
        cuda_threads=768,
        expected_hashrates={
            "ethash": 98e6,      # 98 MH/s
            "etchash": 98e6,
            "kawpow": 48e6,      # 48 MH/s
            "autolykos2": 210e6, # 210 MH/s
            "kheavyhash": 680e6, # 680 MH/s
        },
        temp_limit_c=83,
        temp_target_c=70,
        expected_power_watts=250,
        efficiency_score=0.39,
    ),
    
    "RTX 3090": GPUMiningProfile(
        gpu_type=CloudGPUType.LOCAL_GPU,
        gpu_name="GeForce RTX 3090",
        core_clock_offset=-200,
        memory_clock_offset=1100,
        power_limit_percent=73,
        fan_speed_percent=75,
        intensity=100,
        cuda_threads=768,
        expected_hashrates={
            "ethash": 125e6,     # 125 MH/s
            "etchash": 125e6,
            "kawpow": 52e6,      # 52 MH/s
            "autolykos2": 245e6, # 245 MH/s
            "kheavyhash": 750e6, # 750 MH/s
        },
        temp_limit_c=83,
        temp_target_c=70,
        expected_power_watts=290,
        efficiency_score=0.43,
    ),
    
    "RTX 3080": GPUMiningProfile(
        gpu_type=CloudGPUType.LOCAL_GPU,
        gpu_name="GeForce RTX 3080",
        core_clock_offset=-200,
        memory_clock_offset=1000,
        power_limit_percent=70,
        fan_speed_percent=75,
        intensity=100,
        cuda_threads=768,
        expected_hashrates={
            "ethash": 100e6,     # 100 MH/s
            "etchash": 100e6,
            "kawpow": 42e6,      # 42 MH/s
            "autolykos2": 195e6, # 195 MH/s
            "kheavyhash": 600e6, # 600 MH/s
        },
        temp_limit_c=83,
        temp_target_c=70,
        expected_power_watts=230,
        efficiency_score=0.43,
    ),
    
    "RTX 3070": GPUMiningProfile(
        gpu_type=CloudGPUType.LOCAL_GPU,
        gpu_name="GeForce RTX 3070",
        core_clock_offset=-200,
        memory_clock_offset=800,
        power_limit_percent=65,
        fan_speed_percent=70,
        intensity=100,
        cuda_threads=512,
        expected_hashrates={
            "ethash": 62e6,      # 62 MH/s
            "etchash": 62e6,
            "kawpow": 30e6,      # 30 MH/s
            "autolykos2": 140e6, # 140 MH/s
            "kheavyhash": 450e6, # 450 MH/s
        },
        temp_limit_c=83,
        temp_target_c=68,
        expected_power_watts=130,
        efficiency_score=0.48,
    ),
    
    # ═══════════════════════════════════════════════════════════════
    # AMD GPUs
    # ═══════════════════════════════════════════════════════════════
    
    "RX 7900 XTX": GPUMiningProfile(
        gpu_type=CloudGPUType.GENERIC_AMD,
        gpu_name="Radeon RX 7900 XTX",
        core_clock_offset=-100,
        memory_clock_offset=0,  # AMD uses absolute values
        power_limit_percent=80,
        fan_speed_percent=70,
        intensity=100,
        worksize=256,
        expected_hashrates={
            "ethash": 85e6,      # 85 MH/s
            "etchash": 85e6,
            "kawpow": 38e6,      # 38 MH/s
            "autolykos2": 175e6, # 175 MH/s
            "kheavyhash": 550e6, # 550 MH/s
        },
        temp_limit_c=90,
        temp_target_c=75,
        expected_power_watts=280,
        efficiency_score=0.30,
    ),
    
    "RX 6900 XT": GPUMiningProfile(
        gpu_type=CloudGPUType.GENERIC_AMD,
        gpu_name="Radeon RX 6900 XT",
        core_clock_offset=-100,
        memory_clock_offset=0,
        power_limit_percent=75,
        fan_speed_percent=75,
        intensity=100,
        worksize=256,
        expected_hashrates={
            "ethash": 64e6,      # 64 MH/s
            "etchash": 64e6,
            "kawpow": 28e6,      # 28 MH/s
            "autolykos2": 135e6, # 135 MH/s
            "kheavyhash": 420e6, # 420 MH/s
        },
        temp_limit_c=90,
        temp_target_c=75,
        expected_power_watts=220,
        efficiency_score=0.29,
    ),
    
    "RX 6800 XT": GPUMiningProfile(
        gpu_type=CloudGPUType.GENERIC_AMD,
        gpu_name="Radeon RX 6800 XT",
        core_clock_offset=-100,
        memory_clock_offset=0,
        power_limit_percent=75,
        fan_speed_percent=75,
        intensity=100,
        worksize=256,
        expected_hashrates={
            "ethash": 63e6,      # 63 MH/s
            "etchash": 63e6,
            "kawpow": 27e6,      # 27 MH/s
            "autolykos2": 130e6, # 130 MH/s
            "kheavyhash": 400e6, # 400 MH/s
        },
        temp_limit_c=90,
        temp_target_c=75,
        expected_power_watts=200,
        efficiency_score=0.32,
    ),
    
    # Instinct MI series (AMD Data Center)
    "MI250X": GPUMiningProfile(
        gpu_type=CloudGPUType.GENERIC_AMD,
        gpu_name="AMD Instinct MI250X",
        core_clock_offset=0,
        memory_clock_offset=0,
        power_limit_percent=85,
        fan_speed_percent=80,
        intensity=100,
        worksize=512,
        expected_hashrates={
            "ethash": 180e6,     # 180 MH/s (dual GPU)
            "etchash": 180e6,
            "kawpow": 55e6,      # 55 MH/s
            "autolykos2": 350e6, # 350 MH/s
        },
        temp_limit_c=85,
        temp_target_c=75,
        expected_power_watts=500,
        efficiency_score=0.36,
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# GPU Optimizer Class
# ══════════════════════════════════════════════════════════════════════════════

class GPUOptimizer:
    """
    Advanced GPU optimization for maximum mining efficiency.
    
    This class handles:
    - Automatic profile selection based on detected GPU
    - Dynamic parameter tuning based on thermals and performance
    - Cloud-specific optimizations
    - Multi-GPU coordination
    - Auto-tuning for optimal hashrate
    """
    
    def __init__(self):
        self._profiles: Dict[int, GPUMiningProfile] = {}
        self._tuning_state: Dict[int, dict] = {}
        self._lock = threading.Lock()
        self._monitoring = False
        self._monitor_thread: Optional[threading.Thread] = None
        
        # Performance tracking
        self._hashrate_history: Dict[int, List[Tuple[float, float]]] = {}  # device_id -> [(timestamp, hashrate)]
        self._temp_history: Dict[int, List[Tuple[float, float]]] = {}
        
        # Cloud environment
        self._cloud_provider = self._detect_cloud_provider()
        
        # NVIDIA tools availability
        self._has_nvidia_smi = shutil.which("nvidia-smi") is not None
        self._has_nvidia_settings = shutil.which("nvidia-settings") is not None
        
        # AMD tools availability
        self._has_rocm_smi = shutil.which("rocm-smi") is not None
    
    def _detect_cloud_provider(self) -> Optional[str]:
        """Detect which cloud provider we're running on."""
        # AWS
        if os.path.exists("/sys/hypervisor/uuid"):
            try:
                with open("/sys/hypervisor/uuid", "r") as f:
                    if f.read().strip().startswith("ec2"):
                        return "aws"
            except Exception:
                pass
        
        # Check for AWS metadata service
        try:
            import urllib.request
            urllib.request.urlopen("http://169.254.169.254/latest/meta-data/", timeout=0.5)
            return "aws"
        except Exception:
            pass
        
        # GCP
        if os.getenv("GOOGLE_CLOUD_PROJECT") or os.path.exists("/etc/google_cloud"):
            return "gcp"
        
        # Azure
        if os.getenv("AZURE_SUBSCRIPTION_ID"):
            return "azure"
        
        # Render.com
        if os.getenv("RENDER"):
            return "render"
        
        return None
    
    def get_profile_for_gpu(self, gpu_name: str, memory_mb: int) -> GPUMiningProfile:
        """
        Get or create an optimized profile for a GPU.
        
        First tries to match a known profile, then creates a dynamic
        profile based on GPU characteristics.
        """
        # Try exact match
        for profile_name, profile in GPU_MINING_PROFILES.items():
            if profile_name.lower() in gpu_name.lower():
                logger.info("Found exact profile match for %s: %s", gpu_name, profile_name)
                return profile
        
        # Try partial match
        for profile_name, profile in GPU_MINING_PROFILES.items():
            # Check if key parts of the name match
            key_parts = profile_name.lower().split()
            gpu_lower = gpu_name.lower()
            if all(part in gpu_lower for part in key_parts if len(part) > 2):
                logger.info("Found partial profile match for %s: %s", gpu_name, profile_name)
                return profile
        
        # Generate dynamic profile
        logger.info("Generating dynamic profile for %s (%d MB)", gpu_name, memory_mb)
        return self._generate_dynamic_profile(gpu_name, memory_mb)
    
    def _generate_dynamic_profile(self, gpu_name: str, memory_mb: int) -> GPUMiningProfile:
        """
        Generate an optimized profile for an unknown GPU based on its specs.
        """
        # Determine vendor
        gpu_lower = gpu_name.lower()
        is_nvidia = any(x in gpu_lower for x in ['nvidia', 'geforce', 'rtx', 'gtx', 'tesla', 'quadro', 'a100', 'a10', 'v100', 't4', 'l4'])
        is_amd = any(x in gpu_lower for x in ['amd', 'radeon', 'rx', 'vega', 'instinct', 'mi'])
        
        # Estimate performance class based on memory
        if memory_mb >= 16384:
            perf_class = "high"
        elif memory_mb >= 8192:
            perf_class = "medium"
        else:
            perf_class = "low"
        
        # Generate hashrate estimates based on memory and class
        base_hashrate = memory_mb * 6  # Rough estimate: 6 H/s per MB for ethash
        
        hashrates = {
            "ethash": base_hashrate,
            "etchash": base_hashrate,
            "kawpow": base_hashrate * 0.35,
            "autolykos2": base_hashrate * 1.8,
            "kheavyhash": base_hashrate * 5.5,
        }
        
        # Adjust based on performance class
        multiplier = {"high": 1.2, "medium": 1.0, "low": 0.8}[perf_class]
        hashrates = {k: v * multiplier for k, v in hashrates.items()}
        
        # Power estimates
        power_watts = {
            "high": 300,
            "medium": 180,
            "low": 100,
        }[perf_class]
        
        return GPUMiningProfile(
            gpu_type=CloudGPUType.GENERIC_NVIDIA if is_nvidia else CloudGPUType.GENERIC_AMD,
            gpu_name=gpu_name,
            core_clock_offset=0 if is_amd else -100,  # Conservative
            memory_clock_offset=0 if is_amd else 500,
            power_limit_percent=85,
            fan_speed_percent=75,
            intensity=95,
            cuda_threads=512 if is_nvidia else 0,
            worksize=256 if is_amd else 0,
            expected_hashrates=hashrates,
            temp_limit_c=85,
            temp_target_c=75,
            expected_power_watts=power_watts,
            efficiency_score=hashrates["ethash"] / power_watts / 1e6,
        )
    
    def apply_profile(self, device_id: int, profile: GPUMiningProfile) -> bool:
        """
        Apply an optimization profile to a GPU.
        
        Note: Most cloud GPU instances don't allow direct overclocking.
        This method applies what it can and records the profile for
        software-level optimizations.
        """
        with self._lock:
            self._profiles[device_id] = profile
            
            applied_settings = []
            
            # Try to apply power limit (often allowed on cloud)
            if self._has_nvidia_smi and profile.power_limit_percent < 100:
                if self._set_nvidia_power_limit(device_id, profile.power_limit_percent):
                    applied_settings.append(f"power_limit={profile.power_limit_percent}%")
            
            # Try to apply fan speed
            if self._has_nvidia_settings:
                if self._set_nvidia_fan_speed(device_id, profile.fan_speed_percent):
                    applied_settings.append(f"fan_speed={profile.fan_speed_percent}%")
            
            # AMD settings
            if self._has_rocm_smi:
                if self._set_amd_power_limit(device_id, profile.power_limit_percent):
                    applied_settings.append(f"power_limit={profile.power_limit_percent}%")
            
            if applied_settings:
                logger.info("Applied GPU %d optimizations: %s", device_id, ", ".join(applied_settings))
            else:
                logger.info("Stored profile for GPU %d (hardware settings not available)", device_id)
            
            return True
    
    def _set_nvidia_power_limit(self, device_id: int, percent: int) -> bool:
        """Set NVIDIA GPU power limit."""
        try:
            # First get the max power limit
            result = subprocess.run(
                ["nvidia-smi", "-i", str(device_id), 
                 "--query-gpu=power.max_limit", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                max_watts = float(result.stdout.strip())
                target_watts = max_watts * percent / 100
                
                # Set the power limit
                result = subprocess.run(
                    ["nvidia-smi", "-i", str(device_id), 
                     f"--power-limit={int(target_watts)}"],
                    capture_output=True, text=True, timeout=5
                )
                return result.returncode == 0
        except Exception as e:
            logger.debug("Failed to set NVIDIA power limit: %s", e)
        return False
    
    def _set_nvidia_fan_speed(self, device_id: int, percent: int) -> bool:
        """Set NVIDIA GPU fan speed (requires X display and nvidia-settings)."""
        try:
            # Enable manual fan control
            subprocess.run(
                ["nvidia-settings", "-a", f"[gpu:{device_id}]/GPUFanControlState=1"],
                capture_output=True, timeout=5
            )
            # Set fan speed
            result = subprocess.run(
                ["nvidia-settings", "-a", f"[fan:{device_id}]/GPUTargetFanSpeed={percent}"],
                capture_output=True, timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug("Failed to set NVIDIA fan speed: %s", e)
        return False
    
    def _set_amd_power_limit(self, device_id: int, percent: int) -> bool:
        """Set AMD GPU power limit."""
        try:
            result = subprocess.run(
                ["rocm-smi", "-d", str(device_id), 
                 "--setpoweroverdrive", str(percent)],
                capture_output=True, text=True, timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            logger.debug("Failed to set AMD power limit: %s", e)
        return False
    
    def get_miner_args(self, device_id: int, algorithm: str) -> List[str]:
        """
        Get optimized miner command-line arguments for a GPU and algorithm.
        """
        profile = self._profiles.get(device_id)
        if not profile:
            return []
        
        args = []
        
        # CUDA-specific args (for NVIDIA)
        if profile.cuda_threads > 0:
            args.extend(["--cuda-affinity", str(device_id)])
            args.extend(["--cuda-schedule", "2"])  # Sync schedule
        
        # Worksize for OpenCL (AMD)
        if profile.worksize > 0:
            args.extend(["--worksize", str(profile.worksize)])
        
        # Intensity
        if profile.intensity < 100:
            args.extend(["--intensity", str(profile.intensity)])
        
        # Temperature limit
        args.extend(["--temperature-limit", str(profile.temp_limit_c)])
        
        return args
    
    def get_expected_hashrate(self, device_id: int, algorithm: str) -> float:
        """Get expected hashrate for a GPU and algorithm."""
        profile = self._profiles.get(device_id)
        if not profile:
            return 0.0
        
        algo_lower = algorithm.lower()
        return profile.expected_hashrates.get(algo_lower, 0.0)
    
    def start_monitoring(self, callback: Optional[callable] = None):
        """Start background monitoring of GPU performance."""
        if self._monitoring:
            return
        
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(callback,),
            daemon=True,
            name="gpu-optimizer-monitor"
        )
        self._monitor_thread.start()
        logger.info("GPU performance monitoring started")
    
    def stop_monitoring(self):
        """Stop GPU monitoring."""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        logger.info("GPU performance monitoring stopped")
    
    def _monitor_loop(self, callback: Optional[callable]):
        """Background loop for monitoring and dynamic adjustment."""
        interval = 10.0  # Check every 10 seconds
        
        while self._monitoring:
            time.sleep(interval)
            
            if not self._monitoring:
                break
            
            with self._lock:
                for device_id, profile in self._profiles.items():
                    try:
                        # Get current temperature
                        temp = self._get_gpu_temperature(device_id)
                        
                        if temp > 0:
                            # Record temperature
                            if device_id not in self._temp_history:
                                self._temp_history[device_id] = []
                            self._temp_history[device_id].append((time.time(), temp))
                            # Keep last 100 readings
                            self._temp_history[device_id] = self._temp_history[device_id][-100:]
                            
                            # Dynamic adjustment based on temperature
                            if temp >= profile.temp_limit_c:
                                self._throttle_gpu(device_id, "temp_limit")
                            elif temp >= profile.temp_target_c + 5:
                                self._reduce_intensity(device_id)
                            elif temp < profile.temp_target_c - 10:
                                self._increase_intensity(device_id)
                        
                        if callback:
                            callback(device_id, {
                                "temperature": temp,
                                "profile": profile.to_dict(),
                            })
                            
                    except Exception as e:
                        logger.debug("Monitor error for GPU %d: %s", device_id, e)
    
    def _get_gpu_temperature(self, device_id: int) -> float:
        """Get current GPU temperature."""
        if self._has_nvidia_smi:
            try:
                result = subprocess.run(
                    ["nvidia-smi", "-i", str(device_id),
                     "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    return float(result.stdout.strip())
            except Exception:
                pass
        
        if self._has_rocm_smi:
            try:
                result = subprocess.run(
                    ["rocm-smi", "-d", str(device_id), "--showtemp"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    match = re.search(r"(\d+\.?\d*)\s*[cC]", result.stdout)
                    if match:
                        return float(match.group(1))
            except Exception:
                pass
        
        return 0.0
    
    def _throttle_gpu(self, device_id: int, reason: str):
        """Emergency throttle a GPU."""
        logger.warning("Throttling GPU %d due to %s", device_id, reason)
        
        profile = self._profiles.get(device_id)
        if profile:
            # Reduce power limit
            new_power = max(50, profile.power_limit_percent - 10)
            if new_power != profile.power_limit_percent:
                profile.power_limit_percent = new_power
                self._set_nvidia_power_limit(device_id, new_power)
            
            # Reduce intensity
            profile.intensity = max(50, profile.intensity - 15)
    
    def _reduce_intensity(self, device_id: int):
        """Reduce mining intensity for a GPU."""
        profile = self._profiles.get(device_id)
        if profile and profile.intensity > 70:
            profile.intensity -= 5
            logger.debug("Reduced GPU %d intensity to %d%%", device_id, profile.intensity)
    
    def _increase_intensity(self, device_id: int):
        """Increase mining intensity for a GPU."""
        profile = self._profiles.get(device_id)
        if profile and profile.intensity < 100:
            profile.intensity = min(100, profile.intensity + 3)
            logger.debug("Increased GPU %d intensity to %d%%", device_id, profile.intensity)
    
    def get_optimization_stats(self) -> dict:
        """Get current optimization statistics."""
        with self._lock:
            return {
                "cloud_provider": self._cloud_provider,
                "has_nvidia_tools": self._has_nvidia_smi,
                "has_amd_tools": self._has_rocm_smi,
                "active_profiles": len(self._profiles),
                "profiles": {
                    device_id: profile.to_dict()
                    for device_id, profile in self._profiles.items()
                },
                "monitoring_active": self._monitoring,
            }


# ══════════════════════════════════════════════════════════════════════════════
# Auto-Tuner for Maximum Hashrate
# ══════════════════════════════════════════════════════════════════════════════

class HashrateTuner:
    """
    Automatic tuning system to maximize hashrate.
    
    Uses gradient-free optimization to find the best parameters
    for a given GPU and algorithm combination.
    """
    
    def __init__(self, optimizer: GPUOptimizer):
        self.optimizer = optimizer
        self._tuning_active = False
        self._best_params: Dict[Tuple[int, str], dict] = {}
        self._lock = threading.Lock()
    
    def auto_tune(
        self,
        device_id: int,
        algorithm: str,
        get_hashrate_fn: callable,
        duration_minutes: int = 5
    ) -> dict:
        """
        Auto-tune a GPU for a specific algorithm.
        
        Args:
            device_id: GPU device ID
            algorithm: Mining algorithm
            get_hashrate_fn: Function that returns current hashrate
            duration_minutes: How long to tune for
        
        Returns:
            Best parameters found
        """
        logger.info("Starting auto-tune for GPU %d on %s (duration: %d min)",
                   device_id, algorithm, duration_minutes)
        
        self._tuning_active = True
        start_time = time.time()
        end_time = start_time + duration_minutes * 60
        
        profile = self.optimizer._profiles.get(device_id)
        if not profile:
            return {}
        
        best_hashrate = 0.0
        best_params = {
            "intensity": profile.intensity,
            "power_limit": profile.power_limit_percent,
        }
        
        # Parameter ranges to test
        intensities = [80, 85, 90, 95, 100]
        power_limits = [70, 75, 80, 85, 90, 95, 100]
        
        # Grid search (simplified for cloud environments)
        tested = 0
        for intensity in intensities:
            if time.time() > end_time:
                break
            
            for power_limit in power_limits:
                if time.time() > end_time:
                    break
                
                if not self._tuning_active:
                    break
                
                # Apply parameters
                profile.intensity = intensity
                profile.power_limit_percent = power_limit
                self.optimizer._set_nvidia_power_limit(device_id, power_limit)
                
                # Wait for hashrate to stabilize
                time.sleep(30)
                
                # Measure hashrate
                hashrate = get_hashrate_fn()
                tested += 1
                
                if hashrate > best_hashrate:
                    best_hashrate = hashrate
                    best_params = {
                        "intensity": intensity,
                        "power_limit": power_limit,
                        "hashrate": hashrate,
                    }
                    logger.info(
                        "New best: intensity=%d, power=%d%%, hashrate=%.2f MH/s",
                        intensity, power_limit, hashrate / 1e6
                    )
        
        # Apply best parameters
        profile.intensity = best_params["intensity"]
        profile.power_limit_percent = best_params["power_limit"]
        self.optimizer._set_nvidia_power_limit(device_id, best_params["power_limit"])
        
        with self._lock:
            self._best_params[(device_id, algorithm)] = best_params
        
        self._tuning_active = False
        
        logger.info(
            "Auto-tune complete for GPU %d. Best: intensity=%d, power=%d%%, hashrate=%.2f MH/s",
            device_id, best_params["intensity"], best_params["power_limit"],
            best_hashrate / 1e6
        )
        
        return best_params
    
    def stop_tuning(self):
        """Stop any active tuning."""
        self._tuning_active = False
    
    def get_best_params(self, device_id: int, algorithm: str) -> Optional[dict]:
        """Get previously found best parameters."""
        with self._lock:
            return self._best_params.get((device_id, algorithm))


# ══════════════════════════════════════════════════════════════════════════════
# Multi-GPU Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

class MultiGPUOrchestrator:
    """
    Orchestrates mining across multiple GPUs for maximum efficiency.
    
    Features:
    - Load balancing across GPUs
    - Different algorithms per GPU if beneficial
    - Coordinated thermal management
    - Failover handling
    """
    
    def __init__(self, optimizer: GPUOptimizer):
        self.optimizer = optimizer
        self._gpu_assignments: Dict[int, str] = {}  # device_id -> algorithm
        self._gpu_status: Dict[int, dict] = {}
        self._lock = threading.Lock()
    
    def assign_algorithms(
        self,
        devices: List[dict],
        available_algorithms: List[str],
        profit_data: Optional[Dict[str, float]] = None
    ) -> Dict[int, str]:
        """
        Assign optimal algorithms to each GPU.
        
        If profit data is available, assigns based on profitability.
        Otherwise, assigns based on efficiency.
        """
        with self._lock:
            assignments = {}
            
            for device in devices:
                device_id = device["device_id"]
                gpu_name = device.get("name", "Unknown")
                memory_mb = device.get("memory_mb", 0)
                
                # Get profile for this GPU
                profile = self.optimizer.get_profile_for_gpu(gpu_name, memory_mb)
                
                # Find best algorithm
                best_algo = None
                best_score = 0.0
                
                for algo in available_algorithms:
                    algo_lower = algo.lower()
                    expected_hashrate = profile.expected_hashrates.get(algo_lower, 0)
                    
                    if expected_hashrate == 0:
                        continue
                    
                    # Calculate score
                    if profit_data and algo_lower in profit_data:
                        # Score by profitability
                        score = profit_data[algo_lower] * expected_hashrate
                    else:
                        # Score by efficiency (H/W)
                        power = profile.expected_power_watts or 1
                        score = expected_hashrate / power
                    
                    if score > best_score:
                        best_score = score
                        best_algo = algo
                
                if best_algo:
                    assignments[device_id] = best_algo
                    self._gpu_assignments[device_id] = best_algo
                    logger.info("Assigned GPU %d (%s) to %s", device_id, gpu_name, best_algo)
            
            return assignments
    
    def get_assignments(self) -> Dict[int, str]:
        """Get current GPU algorithm assignments."""
        with self._lock:
            return self._gpu_assignments.copy()
    
    def update_gpu_status(self, device_id: int, status: dict):
        """Update status for a GPU."""
        with self._lock:
            self._gpu_status[device_id] = {
                **status,
                "last_update": time.time(),
            }
    
    def get_orchestration_stats(self) -> dict:
        """Get multi-GPU orchestration statistics."""
        with self._lock:
            return {
                "gpu_count": len(self._gpu_assignments),
                "assignments": self._gpu_assignments.copy(),
                "gpu_status": self._gpu_status.copy(),
            }


# ══════════════════════════════════════════════════════════════════════════════
# Module-level instances and helpers
# ══════════════════════════════════════════════════════════════════════════════

_gpu_optimizer: Optional[GPUOptimizer] = None
_hashrate_tuner: Optional[HashrateTuner] = None
_multi_gpu_orchestrator: Optional[MultiGPUOrchestrator] = None


def get_gpu_optimizer() -> GPUOptimizer:
    """Get the singleton GPU optimizer instance."""
    global _gpu_optimizer
    if _gpu_optimizer is None:
        _gpu_optimizer = GPUOptimizer()
    return _gpu_optimizer


def get_hashrate_tuner() -> HashrateTuner:
    """Get the singleton hashrate tuner instance."""
    global _hashrate_tuner
    if _hashrate_tuner is None:
        _hashrate_tuner = HashrateTuner(get_gpu_optimizer())
    return _hashrate_tuner


def get_multi_gpu_orchestrator() -> MultiGPUOrchestrator:
    """Get the singleton multi-GPU orchestrator instance."""
    global _multi_gpu_orchestrator
    if _multi_gpu_orchestrator is None:
        _multi_gpu_orchestrator = MultiGPUOrchestrator(get_gpu_optimizer())
    return _multi_gpu_orchestrator


def get_optimal_settings_for_gpu(gpu_name: str, memory_mb: int, algorithm: str) -> dict:
    """
    Quick helper to get optimal settings for a GPU and algorithm.
    
    Returns a dict with recommended settings.
    """
    optimizer = get_gpu_optimizer()
    profile = optimizer.get_profile_for_gpu(gpu_name, memory_mb)
    
    return {
        "gpu_name": gpu_name,
        "algorithm": algorithm,
        "intensity": profile.intensity,
        "power_limit_percent": profile.power_limit_percent,
        "fan_speed_percent": profile.fan_speed_percent,
        "cuda_threads": profile.cuda_threads,
        "worksize": profile.worksize,
        "expected_hashrate": profile.expected_hashrates.get(algorithm.lower(), 0),
        "expected_power_watts": profile.expected_power_watts,
        "temp_limit_c": profile.temp_limit_c,
        "miner_args": optimizer.get_miner_args(0, algorithm),
    }


def get_all_gpu_profiles() -> Dict[str, dict]:
    """Get all predefined GPU profiles."""
    return {
        name: profile.to_dict()
        for name, profile in GPU_MINING_PROFILES.items()
    }
