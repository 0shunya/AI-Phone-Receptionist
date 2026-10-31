import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { StateStrip, ToolCallList } from "../components/callbits";
import { addDays, compactArgs, isoDay, localTime, minutesOfDay, ms } from "../lib/format";
import { closeMessage, isEcho } from "../lib/useCall";

describe("format", () => {
  it("shows UTC instants in the salon's local time", () => {
    expect(localTime("2026-11-03T04:30:00Z")).toMatch(/^10:00\s?AM$/);
    expect(minutesOfDay("2026-11-03T04:30:00Z")).toBe(600);
    expect(isoDay(new Date("2026-11-02T19:00:00Z"))).toBe("2026-11-03");   // already the 3rd in India
  });

  it("adds days across month ends", () => {
    expect(addDays("2026-10-31", 1)).toBe("2026-11-01");
    expect(addDays("2026-11-01", -1)).toBe("2026-10-31");
  });

  it("formats latency and shortens long ids", () => {
    expect(ms(840)).toBe("840 ms");
    expect(ms(1840)).toBe("1.84 s");
    expect(ms(null)).toBe("—");
    expect(compactArgs({ slot_id: "slot_1", booking_id: "9ad5503e-644c-4452-b0b1-2c00cc30ea17" }))
      .toBe("slot_id=slot_1, booking_id=9ad5503e-644…cc30ea17");
  });
});

describe("barge-in echo filter", () => {
  it("ignores the agent's own words picked up by the mic", () => {
    expect(isEcho("the earliest I have is tomorrow", "For a haircut, the earliest I have is tomorrow at 10 AM")).toBe(true);
  });
  it("lets real interruptions through", () => {
    expect(isEcho("no wait actually Thursday", "For a haircut, the earliest I have is tomorrow at 10 AM")).toBe(false);
    expect(isEcho("hello", "")).toBe(false);
  });
});

describe("call widgets", () => {
  it("marks the current state and keeps handoff separate", () => {
    render(<StateStrip state="CONFIRM" />);
    expect(screen.getByText("Confirm")).toHaveAttribute("aria-current", "step");
    expect(screen.getByLabelText("Current step: Confirm")).toBeInTheDocument();
    expect(screen.getByText("Handoff")).toBeInTheDocument();
  });

  it("lists tool calls with refusals and guard rules", () => {
    render(<ToolCallList calls={[
      { id: "1", turn_seq: 2, tool: "find_slots", args: { service: "haircut" }, ok: true, error_code: null, state: "INTENT", latency_ms: 31, say: null, actor: "model" },
      { id: "2", turn_seq: 4, tool: "book", args: { slot_id: "slot_9" }, ok: false, error_code: "unknown_slot", state: "CHECK_SLOTS", latency_ms: 4, say: null, actor: "model" },
      { id: "3", turn_seq: 6, tool: "transfer_to_human", args: { reason: "caller_request" }, ok: true, error_code: null, state: "COLLECT", latency_ms: 2, say: null, actor: "guard" },
    ]} />);
    expect(screen.getByText("unknown_slot")).toBeInTheDocument();
    expect(screen.getByLabelText("refused")).toBeInTheDocument();
    expect(screen.getByText("guard rule")).toBeInTheDocument();
  });
});

describe("stream close codes", () => {
  it("explains server refusals and stays quiet on a normal close", () => {
    expect(closeMessage(4401)).toMatch(/access code/);
    expect(closeMessage(4403)).toMatch(/FRONTEND_ORIGIN/);
    expect(closeMessage(4404)).toMatch(/no longer exists/);
    expect(closeMessage(1000)).toBeNull();
  });
});
