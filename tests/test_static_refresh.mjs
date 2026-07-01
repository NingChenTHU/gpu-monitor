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
    let scheduledRefresh;
    let holdNextServersResponse = false;

    globalThis.document = {
        querySelector(selector) {
            return selector === "#server-grid" ? serverGrid : null;
        },
    };
    globalThis.setInterval = (callback) => {
        scheduledRefresh = callback;
        return 1;
    };
    globalThis.fetch = async (url) => {
        if (url === "/api/config") {
            return {
                ok: true,
                async json() {
                    return { poll_interval_seconds: 20 };
                },
            };
        }
        if (url === "/api/servers") {
            if (holdNextServersResponse) {
                return new Promise(() => {});
            }
            return {
                ok: true,
                async json() {
                    return [];
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

    holdNextServersResponse = true;
    scheduledRefresh();

    assert.equal(serverGrid.innerHTML, '<p class="empty">No servers configured yet.</p>');
});

test("refresh reuses existing server cards for stable server names", async () => {
    const serverGrid = new FakeElement();
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
            return selector === "#server-grid" ? serverGrid : null;
        },
        createElement() {
            return new FakeElement();
        },
    };
    globalThis.setInterval = (callback) => {
        scheduledRefresh = callback;
        return 1;
    };
    globalThis.fetch = async (url) => {
        if (url === "/api/config") {
            return {
                ok: true,
                async json() {
                    return { poll_interval_seconds: 20 };
                },
            };
        }
        if (url === "/api/servers") {
            serverRequests += 1;
            return {
                ok: true,
                async json() {
                    return [
                        makeServer("gpu-a", serverRequests === 1 ? 1000 : 2000),
                        makeServer("gpu-b", 3000),
                    ];
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
