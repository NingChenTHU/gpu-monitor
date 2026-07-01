import assert from "node:assert/strict";
import test from "node:test";
import { clearInterval as realClearInterval, setInterval as realSetInterval } from "node:timers";
import { pathToFileURL } from "node:url";

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
