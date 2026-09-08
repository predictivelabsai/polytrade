import pg from "pg";

import { AgentPredictionGrader, PublicAgentAccuracyService } from "./agent-accuracy.js";
import { PostgresAgentPredictionStore } from "./agent-prediction-store.js";
import { AlertSender } from "./alert-sender.js";
import { AlertDeliveryRunner, AlertService } from "./alert-service.js";
import { PostgresAlertStore } from "./alert-store.js";
import { createJwtVerifier } from "./auth.js";
import { buildApp } from "./app.js";
import { parseConfig } from "./config.js";
import { CredentialCipher } from "./crypto.js";
import { PostgresPaperStore } from "./paper-store.js";
import { PostgresPaperStrategyStore } from "./paper-strategy-store.js";
import { PaperStrategyBackgroundRunner, PaperStrategyService } from "./paper-strategy.js";
import { PaperTradingService } from "./paper.js";
import { PolymarketAdapter } from "./polymarket.js";
import { PublicMarketService } from "./public-market.js";
import { TtlCache } from "./public-cache.js";
import { PostgresTradingStore } from "./store.js";
import { PublicTrackRecordService } from "./track-record.js";
import { PostgresTrackRecordStore } from "./track-record-store.js";
import { TradingService } from "./trading.js";

const config = parseConfig(process.env);
const pool = new pg.Pool({
  connectionString: config.DATABASE_URL,
  max: 10,
  idleTimeoutMillis: 30_000,
  connectionTimeoutMillis: 5_000,
  application_name: "polytrade-gateway",
  options: "-c search_path=polytrade,public",
});
const store = new PostgresTradingStore(pool);
const cipher = new CredentialCipher(config.credentialKey);
const polymarket = new PolymarketAdapter(config);
const trading = new TradingService(config, store, cipher, polymarket);
const paperStore = new PostgresPaperStore(pool);
const paper = new PaperTradingService(paperStore, polymarket);
const paperStrategyStore = new PostgresPaperStrategyStore(pool);
const paperStrategy = new PaperStrategyService(paperStrategyStore, paper);
const publicMarkets = new PublicMarketService(polymarket, new TtlCache());
const trackRecords = new PublicTrackRecordService(new PostgresTrackRecordStore(pool), new TtlCache());
let reportStrategyError: (error: unknown) => void = () => undefined;
const paperStrategyRunner = new PaperStrategyBackgroundRunner(paperStrategyStore, paper, {
  onError: (error) => reportStrategyError(error),
});
const alertStore = new PostgresAlertStore(pool);
const alerts = new AlertService(alertStore, cipher, new AlertSender(config));
let reportAlertError: (error: unknown) => void = () => undefined;
const alertsRunner = new AlertDeliveryRunner(alerts, {
  onError: (error) => reportAlertError(error),
});
const agentPredictions = new PostgresAgentPredictionStore(pool);
const agentAccuracy = new PublicAgentAccuracyService(agentPredictions, new TtlCache());
let reportGraderError: (error: unknown) => void = () => undefined;
const predictionGrader = new AgentPredictionGrader(agentPredictions, polymarket, {
  onError: (error) => reportGraderError(error),
});
const app = await buildApp({
  config,
  verifier: createJwtVerifier(config),
  store,
  polymarket,
  trading,
  paper,
  paperStrategy,
  publicMarkets,
  trackRecords,
  agentAccuracy,
  paperStrategyRunner,
  alerts,
  alertsRunner,
  predictionGrader,
});
reportStrategyError = (error) => app.log.error({ err: error }, "paper strategy runner failed");
reportAlertError = (error) => app.log.error({ err: error }, "alert delivery runner failed");
reportGraderError = (error) => app.log.error({ err: error }, "prediction grader failed");
paperStrategyRunner.start();
alertsRunner.start();
predictionGrader.start();

let shuttingDown = false;
const shutdown = async (signal: string) => {
  if (shuttingDown) return;
  shuttingDown = true;
  app.log.info({ signal }, "gateway shutdown requested");
  try {
    await app.close();
  } catch (error) {
    app.log.error({ err: error }, "gateway shutdown failed");
    process.exitCode = 1;
  }
};
process.once("SIGINT", () => void shutdown("SIGINT"));
process.once("SIGTERM", () => void shutdown("SIGTERM"));

await app.listen({ host: "0.0.0.0", port: config.PORT });
