import assert from "node:assert/strict";
import test from "node:test";
import { clearInterval as realClearInterval, setInterval as realSetInterval } from "node:timers";
import { pathToFileURL } from "node:url";

class FakeElement {
    constructor() {
        this.children = [];
        this.className = "";
        this.textContent = "";
        this.clearWrites = 0;
        this._innerHTML = "";
    }

    get innerHTML() {
        return this._innerHTML;
    }

    set innerHTML(value) {
        this._innerHTML = value;
        this.children = [];
        if (value === "") {
            this.clearWrites += 1;
        }
    }

    appendChild(child) {
        this.children.push(child);
        return child;
    }

    replaceChildren(...children) {
        this.children = children;
        this._innerHTML = "";
    }

    querySelector() {
        return new FakeElement();
    }
}

class FakeButton {
    constructor() {
        this.disabled = false;
        this._listeners = new Map();
    }

    addEventListener(event, callback) {
        this._listeners.set(event, callback);
    }

    async click() {
        const callback = this._listeners.get("click");
        if (callback) {
            await callback();
        }
    }
}

function waitFor(condition) {
    return new Promise((resolve, reject) => {
        const started = Date.now();
        const timer = realSetInterval(() => {
            if (condition()) {
                realClearInterval(timer);
                resolve();
            } else if (Date.now() - started > 1000) {
                realClearInterval(timer);
                reject(new Error("Timed out waiting for condition"));
            }
        }, 0);
    });
}

test("scheduled refresh keeps existing content while fetching new data", async () => {
    const serverGrid = { innerHTML: "" };
    const refreshButton = new FakeButton();
    let scheduledRefresh;

    globalThis.document = {
        querySelector(selector) {
            if (selector === "#server-grid") {
                return serverGrid;
            }
            if (selector === "#refresh-button") {
                return refreshButton;
            }
            return null;
        },
    };
    globalThis.setInterval = (callback) => {
        scheduledRefresh = callback;
        return 1;
    };
    globalThis.fetch = async (url, options) => {
        if (url === "/api/config") {
            return {
                ok: true,
                async json() {
                    return { poll_interval_seconds: 20, servers: [] };
                },
            };
        }
        throw new Error(`Unexpected URL: ${url}`);
    };

    const appUrl = pathToFileURL("gpu_monitor/static/app.js");
    appUrl.search = `?t=${Date.now()}`;
    await import(appUrl.href);
    await waitFor(() => typeof scheduledRefresh === "function");

    assert.equal(serverGrid.innerHTML, '<p class="empty">No servers configured yet.</p>');

    scheduledRefresh();

    assert.equal(serverGrid.innerHTML, '<p class="empty">No servers configured yet.</p>');
});

test("refresh reuses existing server cards for stable server names", async () => {
    const serverGrid = new FakeElement();
    const refreshButton = new FakeButton();
    let scheduledRefresh;
    let serverRequests = 0;

    const makeServer = (name, memoryUsedMb) => ({
        name,
        last_seen: "2026-01-01T12:00:00Z",
        is_stale: false,
        warnings: [],
        gpus: [
            {
                index: 0,
                name: "NVIDIA RTX A6000",
                memory_used_mb: memoryUsedMb,
                memory_total_mb: 49152,
                utilization_percent: 82,
                processes: [],
            },
        ],
    });

    globalThis.document = {
        querySelector(selector) {
            if (selector === "#server-grid") {
                return serverGrid;
            }
            if (selector === "#refresh-button") {
                return refreshButton;
            }
            return null;
        },
        createElement() {
            return new FakeElement();
        },
    };
    globalThis.setInterval = (callback) => {
        scheduledRefresh = callback;
        return 1;
    };
    globalThis.fetch = async (url, options) => {
        if (url === "/api/config") {
            return {
                ok: true,
                async json() {
                    return { poll_interval_seconds: 20, servers: ["gpu-a", "gpu-b"] };
                },
            };
        }
        if (url === "/api/servers/gpu-a/refresh" && options?.method === "POST") {
            serverRequests += 1;
            return {
                ok: true,
                async json() {
                    return makeServer("gpu-a", serverRequests === 1 ? 1000 : 2000);
                },
            };
        }
        if (url === "/api/servers/gpu-b/refresh" && options?.method === "POST") {
            return {
                ok: true,
                async json() {
                    return makeServer("gpu-b", 3000);
                },
            };
        }
        throw new Error(`Unexpected URL: ${url}`);
    };

    const appUrl = pathToFileURL("gpu_monitor/static/app.js");
    appUrl.search = `?t=${Date.now()}-reuse`;
    await import(appUrl.href);
    await waitFor(() => typeof scheduledRefresh === "function");

    const firstCards = [...serverGrid.children];
    serverGrid.clearWrites = 0;

    await scheduledRefresh();

    assert.equal(serverGrid.clearWrites, 0);
    assert.equal(serverGrid.children[0], firstCards[0]);
    assert.equal(serverGrid.children[1], firstCards[1]);
});

test("per-server refresh renders fast servers before slow servers finish", async () => {
    const serverGrid = new FakeElement();
    const refreshButton = new FakeButton();
    let scheduledRefresh;
    let resolveSlowRefresh;

    const makeServer = (name, memoryUsedMb) => ({
        name,
        last_seen: "2026-01-01T12:00:00Z",
        is_stale: false,
        warnings: [],
        gpus: [
            {
                index: 0,
                name: "NVIDIA RTX A6000",
                memory_used_mb: memoryUsedMb,
                memory_total_mb: 49152,
                utilization_percent: 82,
                processes: [],
            },
        ],
    });

    globalThis.document = {
        querySelector(selector) {
            if (selector === "#server-grid") {
                return serverGrid;
            }
            if (selector === "#refresh-button") {
                return refreshButton;
            }
            return null;
        },
        createElement() {
            return new FakeElement();
        },
    };
    globalThis.setInterval = (callback) => {
        scheduledRefresh = callback;
        return 1;
    };
    globalThis.fetch = async (url, options) => {
        if (url === "/api/config") {
            return {
                ok: true,
                async json() {
                    return { poll_interval_seconds: 20, servers: ["gpu-a", "gpu-b"] };
                },
            };
        }
        if (url === "/api/servers/gpu-a/refresh" && options?.method === "POST") {
            return new Promise((resolve) => {
                resolveSlowRefresh = () =>
                    resolve({
                        ok: true,
                        async json() {
                            return makeServer("gpu-a", 2000);
                        },
                    });
            });
        }
        if (url === "/api/servers/gpu-b/refresh" && options?.method === "POST") {
            return {
                ok: true,
                async json() {
                    return makeServer("gpu-b", 3000);
                },
            };
        }
        throw new Error(`Unexpected URL: ${url}`);
    };

    const appUrl = pathToFileURL("gpu_monitor/static/app.js");
    appUrl.search = `?t=${Date.now()}-partial`;
    await import(appUrl.href);
    await waitFor(() => typeof scheduledRefresh === "function" && serverGrid.children.length === 2);

    const firstCards = [...serverGrid.children];
    firstCards[0].clearWrites = 0;
    firstCards[1].clearWrites = 0;

    scheduledRefresh();
    await waitFor(() => firstCards[1].clearWrites > firstCards[0].clearWrites);

    assert.equal(firstCards[0].className.includes("refreshing"), true);
    assert.equal(firstCards[1].className.includes("refreshing"), false);

    resolveSlowRefresh();
    await waitFor(() => !firstCards[0].className.includes("refreshing"));
});

test("manual refresh leaves button enabled and skips servers already refreshing", async () => {
    const serverGrid = new FakeElement();
    const refreshButton = new FakeButton();
    let forceRefreshRequests = 0;
    let resolveForceRefresh;

    globalThis.document = {
        querySelector(selector) {
            if (selector === "#server-grid") {
                return serverGrid;
            }
            if (selector === "#refresh-button") {
                return refreshButton;
            }
            return null;
        },
        createElement() {
            return new FakeElement();
        },
    };
    globalThis.setInterval = () => 1;
    globalThis.fetch = async (url, options) => {
        if (url === "/api/config") {
            return {
                ok: true,
                async json() {
                    return { poll_interval_seconds: 20, servers: ["gpu-a"] };
                },
            };
        }
        if (url === "/api/servers/gpu-a/refresh" && options?.method === "POST") {
            return {
                ok: true,
                async json() {
                    return {
                        name: "gpu-a",
                        last_seen: null,
                        is_stale: true,
                        warnings: ["Waiting for first GPU data"],
                        gpus: [],
                    };
                },
            };
        }
        if (url === "/api/servers/gpu-a/refresh?force=true" && options?.method === "POST") {
            forceRefreshRequests += 1;
            return new Promise((resolve) => {
                resolveForceRefresh = () =>
                    resolve({
                        ok: true,
                        async json() {
                            return {
                                name: "gpu-a",
                                last_seen: null,
                                is_stale: true,
                                warnings: ["Waiting for first GPU data"],
                                gpus: [],
                            };
                        },
                    });
            });
        }
        throw new Error(`Unexpected URL: ${url}`);
    };

    const appUrl = pathToFileURL("gpu_monitor/static/app.js");
    appUrl.search = `?t=${Date.now()}-manual`;
    await import(appUrl.href);
    await waitFor(() => serverGrid.children.length === 1);

    const clickPromise = refreshButton.click();
    await waitFor(() => serverGrid.children[0].className.includes("refreshing"));

    assert.equal(refreshButton.disabled, false);
    assert.equal(forceRefreshRequests, 1);

    await refreshButton.click();

    assert.equal(forceRefreshRequests, 1);

    resolveForceRefresh();
    await clickPromise;

    assert.equal(serverGrid.children[0].className.includes("refreshing"), false);
    assert.equal(refreshButton.disabled, false);
});
