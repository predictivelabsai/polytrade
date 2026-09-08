import { randomBytes } from "node:crypto";

import { describe, expect, it } from "vitest";

import { CredentialCipher } from "../src/crypto.js";

describe("CredentialCipher", () => {
  it("round-trips credentials only with matching associated data", () => {
    const cipher = new CredentialCipher(randomBytes(32));
    const encrypted = cipher.encrypt({ key: "api", secret: "secret", passphrase: "pass" }, "owner:session");

    expect(cipher.decrypt(encrypted, "owner:session")).toEqual({
      key: "api",
      secret: "secret",
      passphrase: "pass",
    });
    expect(() => cipher.decrypt(encrypted, "another:session")).toThrow();
    expect(encrypted).not.toContain("secret");
  });
});
