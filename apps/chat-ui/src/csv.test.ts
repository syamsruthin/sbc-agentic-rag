import { describe, expect, it } from "vitest";

import { toCsv } from "./csv";

describe("toCsv", () => {
  it("quotes commas, quotes and new lines, and keeps the cell text otherwise", () => {
    expect(toCsv([["Service", "Plan A"], ["Specialist visit", '$50 copay, then "20%"']])).toBe(
      'Service,Plan A\r\nSpecialist visit,"$50 copay, then ""20%"""\r\n',
    );
  });

  it("neutralizes cells a spreadsheet would run as formulas", () => {
    expect(toCsv([["=HYPERLINK(1)", "+1", "@x", "No charge"]])).toBe("'=HYPERLINK(1),'+1,'@x,No charge\r\n");
  });
});
