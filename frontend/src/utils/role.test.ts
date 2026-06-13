import { describe, it, expect } from "vitest";
import { roleAtLeast } from "./role";

describe("roleAtLeast", () => {
  it("super_admin >= admin", () => {
    expect(roleAtLeast("super_admin", "admin")).toBe(true);
  });
  it("admin >= admin", () => {
    expect(roleAtLeast("admin", "admin")).toBe(true);
  });
  it("user < admin", () => {
    expect(roleAtLeast("user", "admin")).toBe(false);
  });
  it("undefined < user", () => {
    expect(roleAtLeast(undefined, "user")).toBe(false);
  });
});
