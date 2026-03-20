"""
Chat UI styles — light theme, simplified.
"""

from fasthtml.common import Style

CHAT_UI_STYLES = """
/* === Chat UI — Light Only === */
.chat-container {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #ffffff;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  overflow: hidden;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 1rem;
  background: #ffffff;
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
  border-radius: 1.125rem;
  font-size: 0.875rem;
  line-height: 1.5;
  word-wrap: break-word;
  position: relative;
}

.chat-message-content p { margin: 0 0 0.5rem 0; }
.chat-message-content p:last-child { margin-bottom: 0; }
.chat-message-content ul, .chat-message-content ol { margin: 0.5rem 0; padding-left: 1.5rem; }
.chat-message-content li { margin: 0.25rem 0; }

.chat-message-content code {
  background: #f1f5f9;
  padding: 0.125rem 0.25rem;
  border-radius: 0.25rem;
  font-size: 0.875em;
}

.chat-message-content pre {
  background: #f8fafc;
  color: #1e293b;
  border: 1px solid #e2e8f0;
  padding: 0.75rem;
  border-radius: 0.5rem;
  overflow-x: auto;
  margin: 0.5rem 0;
  font-size: 0.8rem;
  line-height: 1.5;
}

.chat-message-content pre code { background: none; padding: 0; color: inherit; }

.chat-message-content blockquote {
  border-left: 3px solid #e2e8f0;
  padding-left: 1rem;
  margin: 0.5rem 0;
  color: #64748b;
}

.chat-message-content h1, .chat-message-content h2,
.chat-message-content h3, .chat-message-content h4 {
  margin: 0.75rem 0 0.5rem 0; font-weight: 600; color: #1e293b;
}
.chat-message-content h1 { font-size: 1.25rem; }
.chat-message-content h2 { font-size: 1.125rem; }
.chat-message-content h3 { font-size: 1rem; }

.chat-message-content table {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
  background: #ffffff;
  display: block;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.chat-message-content th, .chat-message-content td {
  border: 1px solid #e2e8f0;
  padding: 0.5rem;
  text-align: left;
  color: #1e293b;
}
.chat-message-content th {
  background: #f8fafc;
  font-weight: 600;
}

@keyframes chat-message-in {
  from { opacity: 0; transform: translateY(0.5rem); }
  to { opacity: 1; transform: translateY(0); }
}

.chat-user { align-self: flex-end; }
.chat-assistant { align-self: flex-start; }

.chat-user .chat-message-content {
  background: #3b82f6;
  color: #ffffff;
  border-bottom-right-radius: 0.375rem;
}

.chat-assistant .chat-message-content {
  background: #f8fafc;
  color: #1e293b;
  border: 1px solid #e2e8f0;
  border-bottom-left-radius: 0.375rem;
}

/* Streaming indicator */
.chat-streaming::after {
  content: '|';
  animation: chat-blink 1s infinite;
  opacity: 0.7;
}

@keyframes chat-blink {
  0%, 50% { opacity: 0.7; }
  51%, 100% { opacity: 0; }
}

/* === Input Form === */
.chat-input {
  padding: 1rem;
  background: #ffffff;
  border-top: 1px solid #e2e8f0;
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
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 1.25rem;
  color: #3b82f6;
  font-size: 0.8rem;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.2s;
}

.suggestion-btn .arrow {
  transition: transform 0.2s;
  font-size: 0.75rem;
}

.suggestion-btn:hover {
  background: #3b82f6;
  color: white;
  border-color: #3b82f6;
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
  border: 1px solid #e2e8f0;
  border-radius: 0.75rem;
  background: #f8fafc;
  color: #1e293b;
  font-family: inherit;
  font-size: 0.9rem;
  line-height: 1.5;
  resize: none;
  min-height: 2.75rem;
  max-height: 12rem;
  overflow-y: hidden;
  box-sizing: border-box;
}

.chat-input-field:focus {
  outline: none;
  border-color: #3b82f6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15);
}

.chat-input-button {
  padding: 0.75rem 1.25rem;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 0.75rem;
  font-family: inherit;
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  min-height: 2.75rem;
}

.chat-input-button:hover { background: #2563eb; }

/* === Tool/System Messages === */
.chat-tool { align-self: center; max-width: 70%; }

.chat-tool .chat-message-content {
  background: #f1f5f9;
  color: #64748b;
  font-size: 0.8rem;
  text-align: center;
  border-radius: 0.75rem;
  padding: 0.4rem 0.8rem;
}

/* === Error States === */
.chat-error .chat-message-content {
  background: #fef2f2;
  color: #dc2626;
  border: 1px solid #fecaca;
}

/* === Log Console (streaming command output) === */
.agui-log-console {
  overflow: visible;
}

.agui-log-pre {
  color: #475569;
  font-size: 0.8em;
  margin: 0;
  white-space: pre-wrap;
  font-family: ui-monospace, monospace;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
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
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  border-radius: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 1.25rem;
}

.welcome-title {
  font-size: 1.5rem;
  font-weight: 700;
  background: linear-gradient(135deg, #1e293b, #3b82f6);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
  margin-bottom: 0.5rem;
}

.welcome-subtitle {
  font-size: 0.875rem;
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
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 1rem;
  cursor: pointer;
  text-align: left;
  transition: all 0.2s;
}

.welcome-card:hover {
  border-color: #93c5fd;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(59, 130, 246, 0.1);
}

.welcome-card-icon {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 0.5rem;
}

.welcome-card-title {
  font-size: 0.825rem;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 0.25rem;
}

.welcome-card-desc {
  font-size: 0.75rem;
  color: #64748b;
}

/* === Input Hint === */
.input-hint {
  font-size: 0.7rem;
  color: #94a3b8;
  text-align: center;
  padding-top: 0.25rem;
}

.kbd {
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 3px;
  padding: 0.1rem 0.35rem;
  font-family: ui-monospace, monospace;
  font-size: 0.65rem;
}

/* === Send Button States === */
.chat-input-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  background: #94a3b8;
}

@keyframes pulse-send {
  0%, 100% { opacity: 0.5; }
  50% { opacity: 0.7; }
}

.chat-input-button.sending {
  animation: pulse-send 1.5s ease-in-out infinite;
  background: #94a3b8;
}

/* === Progress Bar (streaming commands) === */
.progress-bar-container { display: none; margin-bottom: 0.5rem; }
.progress-bar-container.active { display: block; }
.progress-bar-outer { background: #e2e8f0; border-radius: 4px; height: 6px; overflow: hidden; }
.progress-bar-fill { background: linear-gradient(90deg, #3b82f6, #2563eb); height: 100%; border-radius: 4px; transition: width 0.4s ease; width: 0%; }
.progress-bar-label { font-size: 0.7rem; color: #64748b; margin-top: 0.25rem; font-family: monospace; }

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
  color: #3b82f6;
  background: #eff6ff;
  border: 1px solid #dbeafe;
  border-radius: 0.25rem;
  cursor: pointer;
  transition: all 0.15s;
}

.table-action-btn:hover {
  background: #3b82f6;
  color: #fff;
  border-color: #3b82f6;
}

/* === Inline Charts === */
.inline-chart {
  border: 1px solid #e2e8f0;
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
  scrollbar-color: #cbd5e1 transparent;
}

.chat-messages::-webkit-scrollbar { width: 6px; }
.chat-messages::-webkit-scrollbar-track { background: transparent; }
.chat-messages::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
.chat-messages::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

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
