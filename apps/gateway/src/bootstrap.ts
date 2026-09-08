import { readFile } from "node:fs/promises";
import { pathToFileURL, fileURLToPath } from "node:url";

import pg from "pg";

const bootstrapPath = fileURLToPath(new URL("../bootstrap/schema.sql", import.meta.url));

export async function bootstrapDatabase(connectionString: string): Promise<void> {
  if (!connectionString) throw new Error("DATABASE_URL is required");
  const sql = await readFile(bootstrapPath, "utf8");
  const client = new pg.Client({ connectionString, application_name: "polytrade-bootstrap" });
  await client.connect();
  try {
    await client.query("BEGIN");
    try {
      await client.query("SELECT pg_advisory_xact_lock(812704001)");
      await client.query(sql);
      await client.query("COMMIT");
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    }
  } finally {
    await client.end();
  }
}

const invokedPath = process.argv[1];
if (invokedPath && import.meta.url === pathToFileURL(invokedPath).href) {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new Error("DATABASE_URL is required");
  await bootstrapDatabase(connectionString);
}
