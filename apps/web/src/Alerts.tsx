import { Bell, Send, Trash2 } from "lucide-react";
import {
  alertCreateChannelRequestSchema,
  alertEventKindSchema,
  type AlertChannel,
  type AlertCreateChannelRequest,
  type AlertDelivery,
  type AlertEventKind,
} from "@polytrade/contracts";
import { useEffect, useState, type FormEvent } from "react";

import { GatewayClient, GatewayError } from "./api";

const EVENT_KINDS = alertEventKindSchema.options;
const DEFAULT_EVENT_KINDS: AlertEventKind[] = ["BUY", "SELL", "ERROR"];

interface ChannelDraft {
  kind: "discord" | "telegram";
  label: string;
  target: string;
  eventKinds: AlertEventKind[];
}

export function AlertsSettings(props: {
  client: GatewayClient;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [channels, setChannels] = useState<AlertChannel[] | null>(null);
  const [deliveries, setDeliveries] = useState<AlertDelivery[] | null>(null);
  const [draft, setDraft] = useState<ChannelDraft>({
    kind: "discord",
    label: "",
    target: "",
    eventKinds: DEFAULT_EVENT_KINDS,
  });
  const [saving, setSaving] = useState(false);
  const [busyChannelId, setBusyChannelId] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [channelList, deliveryList] = await Promise.all([
          props.client.listAlertChannels(),
          props.client.listAlertDeliveries(),
        ]);
        if (cancelled) return;
        setChannels(channelList.items);
        setDeliveries(deliveryList.items);
      } catch (error) {
        if (!cancelled) props.onError(message(error));
      }
    };
    void load();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshDeliveries = async () => {
    try {
      setDeliveries((await props.client.listAlertDeliveries()).items);
    } catch {
      // The table is secondary — a failed refresh keeps the last snapshot.
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const request = buildRequest(draft);
    if (!request) {
      setFormError(
        draft.kind === "discord"
          ? "Paste an HTTPS Discord webhook URL (discord.com/api/webhooks/…)."
          : "Enter a Telegram chat id (digits, optional leading minus).",
      );
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const created = await props.client.createAlertChannel(request, crypto.randomUUID());
      setChannels((current) => [created, ...(current ?? [])]);
      setDraft({ kind: draft.kind, label: "", target: "", eventKinds: DEFAULT_EVENT_KINDS });
      props.onNotice(`Alert channel “${created.label}” saved.`);
      void refreshDeliveries();
    } catch (error) {
      props.onError(message(error));
    } finally {
      setSaving(false);
    }
  };

  const sendTest = async (channel: AlertChannel) => {
    setBusyChannelId(channel.channelId);
    try {
      const result = await props.client.testAlertChannel(channel.channelId, crypto.randomUUID());
      if (result.status === "sent") {
        props.onNotice(`Test alert sent to “${channel.label}”.`);
      } else {
        props.onError(`Test alert to “${channel.label}” failed: ${result.error ?? "unknown error"}`);
      }
      void refreshDeliveries();
    } catch (error) {
      props.onError(message(error));
    } finally {
      setBusyChannelId(null);
    }
  };

  const remove = async (channel: AlertChannel) => {
    if (!window.confirm(`Delete the “${channel.label}” alert channel? Deliveries stop immediately.`)) return;
    setBusyChannelId(channel.channelId);
    try {
      await props.client.deleteAlertChannel(channel.channelId, crypto.randomUUID());
      setChannels((current) => (current ?? []).filter((item) => item.channelId !== channel.channelId));
      props.onNotice(`Alert channel “${channel.label}” deleted.`);
      void refreshDeliveries();
    } catch (error) {
      props.onError(message(error));
    } finally {
      setBusyChannelId(null);
    }
  };

  const toggleKind = (kind: AlertEventKind) => {
    setDraft((current) => ({
      ...current,
      eventKinds: current.eventKinds.includes(kind)
        ? current.eventKinds.filter((value) => value !== kind)
        : [...current.eventKinds, kind],
    }));
  };

  return (
    <div className="alerts-settings" aria-label="Strategy alerts">
      <p className="settings-copy">
        Get a Discord or Telegram message when a paper strategy starts, stops, trades, or fails.
        Targets are encrypted on the server and never shown again.
      </p>

      {channels === null ? (
        <p className="table-empty">Loading alert channels…</p>
      ) : channels.length === 0 ? (
        <p className="table-empty">No alert channels yet — add one below.</p>
      ) : (
        <ul className="alerts-channel-list">
          {channels.map((channel) => (
            <li key={channel.channelId} className="alerts-channel">
              <div className="alerts-channel-main">
                <span className="status-pill status-pill-open">{channel.label}</span>
                <span className="status-pill">{kindLabel(channel.kind)}</span>
                <code className="alerts-target-hint">{channel.targetHint}</code>
              </div>
              <div className="alerts-channel-kinds">
                {channel.eventKinds.map((kind) => <span key={kind} className="status-pill">{kind}</span>)}
              </div>
              <div className="alerts-channel-actions">
                <button
                  className="button button-quiet"
                  type="button"
                  disabled={busyChannelId === channel.channelId}
                  onClick={() => void sendTest(channel)}
                >
                  <Send aria-hidden="true" /> {busyChannelId === channel.channelId ? "Sending…" : "Send test"}
                </button>
                <button
                  className="table-action"
                  type="button"
                  aria-label={`Delete ${channel.label}`}
                  disabled={busyChannelId === channel.channelId}
                  onClick={() => void remove(channel)}
                >
                  <Trash2 aria-hidden="true" /> Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <form className="settings-form alerts-add-form" onSubmit={(event) => void submit(event)}>
        <h3>Add a channel</h3>
        <label>
          <span>Channel type</span>
          <select
            aria-label="Alert channel type"
            value={draft.kind}
            onChange={(event) => setDraft((current) => ({ ...current, kind: event.target.value as ChannelDraft["kind"] }))}
          >
            <option value="discord">Discord webhook</option>
            <option value="telegram">Telegram chat</option>
          </select>
        </label>
        <label>
          <span>Label</span>
          <input
            aria-label="Alert channel label"
            value={draft.label}
            maxLength={80}
            placeholder={draft.kind === "discord" ? "Trading Discord" : "Phone Telegram"}
            onChange={(event) => setDraft((current) => ({ ...current, label: event.target.value }))}
          />
        </label>
        <label>
          <span>{draft.kind === "discord" ? "Webhook URL" : "Chat id"}</span>
          <input
            aria-label="Alert channel target"
            type={draft.kind === "discord" ? "password" : "text"}
            autoComplete="off"
            spellCheck={false}
            value={draft.target}
            placeholder={draft.kind === "discord" ? "https://discord.com/api/webhooks/…" : "123456789"}
            onChange={(event) => setDraft((current) => ({ ...current, target: event.target.value }))}
          />
        </label>
        <fieldset className="alerts-kind-picker">
          <legend>Notify on</legend>
          {EVENT_KINDS.map((kind) => (
            <label key={kind} className={`alerts-kind-chip ${draft.eventKinds.includes(kind) ? "alerts-kind-on" : ""}`}>
              <input
                type="checkbox"
                checked={draft.eventKinds.includes(kind)}
                onChange={() => toggleKind(kind)}
              />
              <span>{kind}</span>
            </label>
          ))}
        </fieldset>
        <p className="alerts-form-hint">
          {draft.eventKinds.length === 0
            ? "Pick at least one event kind."
            : buildRequest(draft)
              ? "The target is encrypted before it is stored — test it right after saving."
              : draft.kind === "discord"
                ? "Paste an HTTPS Discord webhook URL (discord.com/api/webhooks/…)."
                : "Enter a Telegram chat id (digits, optional leading minus)."}
        </p>
        <button className="button button-primary" type="submit" disabled={saving || draft.eventKinds.length === 0}>
          <Bell aria-hidden="true" /> {saving ? "Saving…" : "Add channel"}
        </button>
      </form>

      <div className="alerts-deliveries">
        <div><span>Recent deliveries</span><small>Updated when this page loads</small></div>
        {deliveries && deliveries.length > 0 ? (
          <ol aria-label="Recent alert deliveries">
            {deliveries.map((delivery) => (
              <li key={delivery.deliveryId} className={`alerts-delivery-${delivery.status}`}>
                <time>{formatTime(delivery.createdAt)}</time>
                <span className={`status-pill status-pill-${delivery.action.toLowerCase()}`}>{delivery.action}</span>
                <span className="alerts-delivery-channel">{delivery.channelLabel}</span>
                <span className="status-pill">{delivery.status}</span>
                <span className="alerts-delivery-message">{delivery.message}</span>
                {delivery.lastError ? <small>{delivery.lastError}</small> : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="table-empty">No alerts delivered yet.</p>
        )}
      </div>
    </div>
  );
}

function buildRequest(draft: ChannelDraft): AlertCreateChannelRequest | null {
  const parsed = alertCreateChannelRequestSchema.safeParse({
    kind: draft.kind,
    label: draft.label.trim(),
    target: draft.target.trim(),
    eventKinds: draft.eventKinds,
  });
  return parsed.success ? parsed.data : null;
}

function kindLabel(kind: AlertChannel["kind"]): string {
  return kind === "discord" ? "Discord webhook" : "Telegram chat";
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

function message(error: unknown): string {
  if (error instanceof GatewayError) return error.message;
  return error instanceof Error ? error.message : "The alert action could not be completed";
}
