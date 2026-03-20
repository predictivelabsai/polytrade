"""
Chat UI styles — dark trading terminal theme for PolyTrade.
"""

from fasthtml.common import Style

CHAT_UI_STYLES = """
/* === Chat UI — Dark Trading Terminal Theme === */
.chat-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #0f1117;
  font-family: 'SF Mono', 'Fira Code', ui-monospace, monospace;
  overflow: hidden;
  color: #e2e8f0;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  background: #0f1117;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

/* === Messages === */
.chat-message {
  display: flex;
  flex-direction: column;
  max-width: 85%;
  animation: chat-message-in 0.3s ease-out;
}

.chat-message-content {
  padding: 0.75rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.85rem;
  line-height: 1.6;
  word-wrap: break-word;
  position: relative;
}

.chat-message-content p { margin: 0 0 0.5rem 0; }
.chat-message-content p:last-child { margin-bottom: 0; }
.chat-message-content ul, .chat-message-content ol { margin: 0.5rem 0; padding-left: 1.5rem; }
.chat-message-content li { margin: 0.25rem 0; }

.chat-message-content code {
  background: #1e2433;
  color: #10b981;
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  font-size: 0.875em;
}

.chat-message-content pre {
  background: #0d1017;
  color: #a5f3c4;
  border: 1px solid #1e2a3a;
  padding: 0.75rem;
  border-radius: 0.5rem;
  overflow-x: auto;
  margin: 0.5rem 0;
  font-size: 0.8rem;
  line-height: 1.5;
}

.chat-message-content pre code { background: none; padding: 0; color: inherit; }

.chat-message-content blockquote {
  border-left: 3px solid #10b981;
  padding-left: 1rem;
  margin: 0.5rem 0;
  color: #94a3b8;
}

.chat-message-content h1, .chat-message-content h2,
.chat-message-content h3, .chat-message-content h4 {
  margin: 0.75rem 0 0.5rem 0; font-weight: 600; color: #10b981;
}
.chat-message-content h1 { font-size: 1.25rem; }
.chat-message-content h2 { font-size: 1.125rem; }
.chat-message-content h3 { font-size: 1rem; }

.chat-message-content table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
  background: #141821;
  display: block;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.chat-message-content th, .chat-message-content td {
  border: 1px solid #1e2a3a;
  padding: 0.5rem;
  text-align: left;
  color: #cbd5e1;
}
.chat-message-content th {
  background: #1a2332;
  font-weight: 600;
  color: #10b981;
}

@keyframes chat-message-in {
  from { opacity: 0; transform: translateY(0.5rem); }
  to { opacity: 1; transform: translateY(0); }
}

.chat-user { align-self: flex-end; }
.chat-assistant { align-self: flex-start; }

.chat-user .chat-message-content {
  background: #064e3b;
  color: #d1fae5;
  border: 1px solid #065f46;
  border-bottom-right-radius: 0.125rem;
}

.chat-assistant .chat-message-content {
  background: #1a1f2e;
  color: #e2e8f0;
  border: 1px solid #2a3040;
  border-bottom-left-radius: 0.125rem;
}

/* Streaming indicator */
.chat-streaming::after {
  content: '_';
  animation: chat-blink 0.6s step-end infinite;
  color: #10b981;
  font-weight: bold;
}

@keyframes chat-blink {
  0%, 50% { opacity: 1; }
  51%, 100% { opacity: 0; }
}

/* === Input Form === */
.chat-input {
  padding: 1rem;
  background: #141821;
  border-top: 1px solid #1e2a3a;
}

.chat-status {
  min-height: 1rem;
  padding: 0.25rem 0;
  color: #64748b;
  font-size: 0.8rem;
  text-align: center;
}

#suggestion-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0.5rem;
  margin-bottom: 0.5rem;
}

.suggestion-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.45rem 0.9rem;
  background: #141821;
  border: 1px solid #2a3040;
  border-radius: 0.375rem;
  color: #10b981;
  font-size: 0.8rem;
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}

.suggestion-btn .arrow {
  transition: transform 0.2s;
  font-size: 0.75rem;
}

.suggestion-btn:hover {
  background: #064e3b;
  color: #d1fae5;
  border-color: #10b981;
}

.suggestion-btn:hover .arrow {
  transform: translateX(2px);
}

.chat-input-form {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.5rem;
  align-items: end;
  width: 100%;
}

.chat-input-field {
  width: 100%;
  padding: 0.75rem 1rem;
  border: 1px solid #2a3040;
  border-radius: 0.5rem;
  background: #0f1117;
  color: #e2e8f0;
  font-family: inherit;
  font-size: 0.9rem;
  line-height: 1.5;
  resize: none;
  min-height: 2.75rem;
  max-height: 12rem;
  overflow-y: hidden;
  box-sizing: border-box;
}

.chat-input-field::placeholder {
  color: #475569;
}

.chat-input-field:focus {
  outline: none;
  border-color: #10b981;
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.15);
}

.chat-input-button {
  padding: 0.75rem 1.25rem;
  background: #059669;
  color: #d1fae5;
  border: none;
  border-radius: 0.5rem;
  font-family: inherit;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  min-height: 2.75rem;
  letter-spacing: 0.025em;
  text-transform: uppercase;
}

.chat-input-button:hover { background: #047857; }

/* === Tool/System Messages === */
.chat-tool { align-self: center; max-width: 70%; }

.chat-tool .chat-message-content {
  background: #1a1f2e;
  color: #64748b;
  font-size: 0.8rem;
  text-align: center;
  border-radius: 0.375rem;
  padding: 0.4rem 0.8rem;
  border: 1px solid #2a3040;
}

/* === Error States === */
.chat-error .chat-message-content {
  background: #1a0a0a;
  color: #f87171;
  border: 1px solid #7f1d1d;
}

/* === Log Console (streaming command output) === */
.agui-log-console {
  overflow: visible;
}

.agui-log-pre {
  color: #94a3b8;
  font-size: 0.8em;
  margin: 0;
  white-space: pre-wrap;
  font-family: inherit;
  background: #0d1017;
  border: 1px solid #1e2a3a;
  padding: 0.75rem;
  border-radius: 0.5rem;
}

/* === Welcome Screen === */
.welcome-hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 560px;
  margin: 0 auto;
  padding-top: 12vh;
  text-align: center;
}

.welcome-icon {
  width: 56px;
  height: 56px;
  background: linear-gradient(135deg, #10b981, #059669);
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1.25rem;
  box-shadow: 0 0 24px rgba(16, 185, 129, 0.3);
}

.welcome-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: #10b981;
  margin-bottom: 0.5rem;
  letter-spacing: 0.05em;
}

.welcome-subtitle {
  font-size: 0.825rem;
  color: #64748b;
  margin-bottom: 2rem;
}

.welcome-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  width: 100%;
}

.welcome-card {
  background: #141821;
  border: 1px solid #2a3040;
  border-radius: 8px;
  padding: 1rem;
  cursor: pointer;
  text-align: left;
  transition: all 0.2s;
}

.welcome-card:hover {
  border-color: #10b981;
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(16, 185, 129, 0.15);
}

.welcome-card-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 0.5rem;
}

.welcome-card-title {
  font-size: 0.825rem;
  font-weight: 600;
  color: #e2e8f0;
  margin-bottom: 0.25rem;
}

.welcome-card-desc {
  font-size: 0.75rem;
  color: #64748b;
}

/* === Input Hint === */
.input-hint {
  font-size: 0.7rem;
  color: #475569;
  text-align: center;
  padding-top: 0.25rem;
}

.kbd {
  background: #1a1f2e;
  border: 1px solid #2a3040;
  border-radius: 3px;
  padding: 0.1rem 0.35rem;
  font-family: inherit;
  font-size: 0.65rem;
  color: #10b981;
}

/* === Send Button States === */
.chat-input-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: #1e2a3a;
  color: #475569;
}

@keyframes pulse-send {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 0.7; }
}

.chat-input-button.sending {
  animation: pulse-send 1.5s ease-in-out infinite;
  background: #1e2a3a;
}

/* === Progress Bar (streaming commands) === */
.progress-bar-container { display: none; margin-bottom: 0.5rem; }
.progress-bar-container.active { display: block; }
.progress-bar-outer { background: #1e2a3a; border-radius: 4px; height: 6px; overflow: hidden; }
.progress-bar-fill { background: linear-gradient(90deg, #10b981, #059669); height: 100%; border-radius: 4px; transition: width 0.4s ease; width: 0%; }
.progress-bar-label { font-size: 0.7rem; color: #64748b; margin-top: 0.25rem; font-family: inherit; }

/* === Table Toolbar (CSV copy/download) === */
.table-toolbar {
  display: flex;
  gap: 0.35rem;
  justify-content: flex-end;
  margin-bottom: 0.25rem;
}

.table-action-btn {
  padding: 0.2rem 0.5rem;
  font-size: 0.7rem;
  font-family: inherit;
  color: #10b981;
  background: #0d1017;
  border: 1px solid #1e2a3a;
  border-radius: 0.25rem;
  cursor: pointer;
  transition: all 0.15s;
}

.table-action-btn:hover {
  background: #064e3b;
  color: #d1fae5;
  border-color: #10b981;
}

/* === Inline Charts === */
.inline-chart {
  border: 1px solid #1e2a3a;
  border-radius: 0.5rem;
}

/* No internal scrollbar on assistant message content */
.chat-assistant .chat-message-content {
  overflow: visible;
  max-height: none;
}

/* Slim scrollbar on chat area */
.chat-messages {
  scrollbar-width: thin;
  scrollbar-color: #2a3040 transparent;
}

.chat-messages::-webkit-scrollbar { width: 6px; }
.chat-messages::-webkit-scrollbar-track { background: transparent; }
.chat-messages::-webkit-scrollbar-thumb { background: #2a3040; border-radius: 3px; }
.chat-messages::-webkit-scrollbar-thumb:hover { background: #3a4050; }

/* === Table column constraints === */
.chat-message-content td:nth-child(3) { max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* === Responsive === */
@media (max-width: 900px) {
  .chat-message-content th:nth-child(5), .chat-message-content td:nth-child(5) { display: none; }
}

@media (max-width: 768px) {
  .chat-message { max-width: 95%; }
  .welcome-grid { grid-template-columns: 1fr; }
}
"""


def get_chat_styles():
    """Get chat UI styles as a Style component."""
    return Style(CHAT_UI_STYLES)
