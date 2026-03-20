/* PolyCode Chat UI — client-side utilities */

/* ── Auto-scroll chat when new messages arrive ─────────────────────────── */
(function () {
    function scrollChat() {
        const el = document.getElementById("chat-messages");
        if (el) el.scrollTop = el.scrollHeight;
    }

    // Scroll on load
    document.addEventListener("DOMContentLoaded", scrollChat);

    // Scroll whenever chat-messages DOM changes
    const obs = new MutationObserver(scrollChat);
    document.addEventListener("DOMContentLoaded", () => {
        const el = document.getElementById("chat-messages");
        if (el) obs.observe(el, { childList: true, subtree: true, characterData: true });
    });
})();

/* ── Auto-resize textarea ───────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
    const ta = document.getElementById("chat-input");
    if (!ta) return;
    ta.addEventListener("input", () => {
        ta.style.height = "auto";
        ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
    });
});

/* ── Format PnL values in trades table ─────────────────────────────────── */
function formatPnl(value) {
    const n = parseFloat(value);
    const formatted = (n >= 0 ? "+" : "") + "$" + Math.abs(n).toFixed(2);
    const cls = n >= 0 ? "pnl-pos" : "pnl-neg";
    return `<span class="${cls}">${formatted}</span>`;
}

/* ── Copy-to-clipboard for code blocks ─────────────────────────────────── */
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("pre code").forEach((block) => {
        const btn = document.createElement("button");
        btn.textContent = "copy";
        btn.className = "copy-btn";
        btn.style.cssText =
            "position:absolute;top:6px;right:8px;background:#334155;color:#94a3b8;" +
            "border:none;border-radius:4px;padding:2px 8px;font-size:0.7rem;cursor:pointer;";
        btn.onclick = () => {
            navigator.clipboard.writeText(block.textContent).then(() => {
                btn.textContent = "copied!";
                setTimeout(() => (btn.textContent = "copy"), 1500);
            });
        };
        const pre = block.parentElement;
        pre.style.position = "relative";
        pre.appendChild(btn);
    });
});
