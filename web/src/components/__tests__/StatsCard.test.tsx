import { render, screen } from "@testing-library/react";
import StatsCard from "../StatsCard";

describe("StatsCard", () => {
  it("renders label and value", () => {
    render(<StatsCard label="Total Workers" value={5} />);
    expect(screen.getByText("Total Workers")).toBeTruthy();
    expect(screen.getByText("5")).toBeTruthy();
  });
});
