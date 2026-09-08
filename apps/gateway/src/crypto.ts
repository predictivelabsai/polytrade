import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";

export class CredentialCipher {
  constructor(
    private readonly key: Buffer,
    private readonly keyVersion = "v1",
  ) {
    if (key.length !== 32) throw new Error("Credential encryption key must be 32 bytes");
  }

  encrypt(value: unknown, associatedData: string): string {
    const iv = randomBytes(12);
    const cipher = createCipheriv("aes-256-gcm", this.key, iv);
    cipher.setAAD(Buffer.from(associatedData, "utf8"));
    const ciphertext = Buffer.concat([
      cipher.update(JSON.stringify(value), "utf8"),
      cipher.final(),
    ]);
    const tag = cipher.getAuthTag();
    return [this.keyVersion, iv.toString("base64url"), tag.toString("base64url"), ciphertext.toString("base64url")].join(".");
  }

  decrypt<T>(envelope: string, associatedData: string): T {
    const [version, ivPart, tagPart, ciphertextPart] = envelope.split(".");
    if (version !== this.keyVersion || !ivPart || !tagPart || !ciphertextPart) {
      throw new Error("Unsupported credential envelope");
    }
    const decipher = createDecipheriv("aes-256-gcm", this.key, Buffer.from(ivPart, "base64url"));
    decipher.setAAD(Buffer.from(associatedData, "utf8"));
    decipher.setAuthTag(Buffer.from(tagPart, "base64url"));
    const plaintext = Buffer.concat([
      decipher.update(Buffer.from(ciphertextPart, "base64url")),
      decipher.final(),
    ]);
    return JSON.parse(plaintext.toString("utf8")) as T;
  }
}
