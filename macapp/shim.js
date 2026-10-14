// Stand-ins for the Brave extension APIs that manager.js uses, backed by the native app (main.swift).
window.chrome = { runtime: { lastError: undefined, sendNativeMessage(_host, msg, cb) {
  window.webkit.messageHandlers.host.postMessage(msg)
    .then((text) => cb(JSON.parse(text)), (err) => cb({ error: String(err) }));
} } };
Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
  readText: () => window.webkit.messageHandlers.host.postMessage({ type: '__clipboard' }).then((t) => JSON.parse(t).text || ''),
} });
