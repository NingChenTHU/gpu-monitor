const serverGrid = document.querySelector("#server-grid");
const refreshButton = document.querySelector("#refresh-button");
const refreshStatus = document.querySelector("#refresh-status");
const serverCards = new Map();
const serverSnapshots = new Map();
const refreshingServers = new Set();
let serverNames = [];

function escapeHtml(text) {
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function formatLastSeen(timestamp) {
    if (!timestamp) {
        return "No data";
    }
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) {
        return "No data";
    }
    return date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
}

function percent(part, total) {
    if (!total || Number.isNaN(total)) {
        return 0;
    }
    return clampPercent(Math.round((part / total) * 100));
}

function clampPercent(value) {
    if (!Number.isFinite(value)) {
        return 0;
    }
    return Math.max(0, Math.min(100, Math.round(value)));
}

function formatMemoryGb(memoryMb) {
    const value = Number(memoryMb);
    if (!Number.isFinite(value)) {
        return 0;
    }
    return Math.max(0, Math.round(value / 1024));
}

function renderPrimaryProcess(processes) {
    if (!processes || !processes.length) {
        return "";
    }
    const primary = [...processes].sort(
        (a, b) => Number(b.memory_mb || 0) - Number(a.memory_mb || 0),
    )[0];
    const procGb = formatMemoryGb(primary.memory_mb);
    const more = processes.length > 1 ? '<span class="process-more">...</span>' : "";
    return `<span>${escapeHtml(primary.user)} · ${procGb} GB</span>${more}`;
}

async function loadAll(force = false) {
    try {
        renderRefreshStatus("");
        renderPlaceholders();
        await refreshServers(force);
    } catch (error) {
        renderRefreshStatus(`Error: ${error.message}`, "error");
    }
}

async function refreshServers(force) {
    await Promise.all(serverNames.map((serverName) => refreshServer(serverName, force)));
}

async function refreshServer(serverName, force) {
    if (refreshingServers.has(serverName)) {
        return;
    }
    const encodedName = encodeURIComponent(serverName);
    const url = `/api/servers/${encodedName}/refresh${force ? "?force=true" : ""}`;
    setServerRefreshing(serverName, true);
    try {
        const response = await fetch(url, { method: "POST" });
        if (!response.ok) {
            throw new Error(`${url} responded with ${response.status}`);
        }
        renderSingleServer(await response.json());
    } catch (error) {
        renderRefreshStatus(`Error: ${error.message}`, "error");
    } finally {
        setServerRefreshing(serverName, false);
    }
}

async function loadConfig() {
    const response = await fetch("/api/config");
    if (!response.ok) {
        throw new Error(`/api/config responded with ${response.status}`);
    }
    const config = await response.json();
    serverNames = config.servers.map((serverName) => String(serverName));
    return Number(config.poll_interval_seconds) * 1000;
}

function renderPlaceholders() {
    if (!serverNames.length) {
        serverCards.clear();
        serverSnapshots.clear();
        serverGrid.innerHTML = '<p class="empty">No servers configured yet.</p>';
        return;
    }
    serverNames.forEach((serverKey) => {
        if (serverCards.has(serverKey)) {
            return;
        }
        const card = document.createElement("div");
        const snapshot = placeholderSnapshot(serverKey);
        serverCards.set(serverKey, card);
        serverSnapshots.set(serverKey, snapshot);
        renderServerCard(card, snapshot);
        serverGrid.appendChild(card);
    });
}

function placeholderSnapshot(serverName) {
    return {
        name: serverName,
        last_seen: null,
        is_stale: true,
        devices: [],
        warnings: ["Waiting for first data"],
    };
}

function renderSingleServer(server) {
    serverSnapshots.set(server.name, server);
    const card = serverCards.get(server.name);
    renderServerCard(card, server);
}

function setServerRefreshing(serverName, isRefreshing) {
    if (isRefreshing) {
        refreshingServers.add(serverName);
    } else {
        refreshingServers.delete(serverName);
    }
    const card = serverCards.get(serverName);
    const snapshot = serverSnapshots.get(serverName);
    if (card && snapshot) {
        renderServerCard(card, snapshot);
    }
}

function renderServerCard(card, server) {
    const hasWarning = Boolean(server.warnings && server.warnings.length);
    const devices = server.devices || [];
    const hasNoDeviceData = !devices.length;
    const isCompact = hasWarning && hasNoDeviceData;
    const isRefreshing = refreshingServers.has(server.name);
    const lastSeen = formatLastSeen(server.last_seen);
    const snapshotAge = isRefreshing
        ? "Refreshing..."
        : server.is_stale ? `Stale · ${lastSeen}` : `Updated ${lastSeen}`;
    card.className = `server-card${server.is_stale ? " stale" : ""}${isCompact ? " compact" : ""}${isRefreshing ? " refreshing" : ""}`;
    card.innerHTML = "";

    const header = document.createElement("div");
    header.className = "server-header";
    header.innerHTML = `
        <div class="server-title">
            <div class="server-identity">
                <h3>${escapeHtml(server.name)}</h3>
            </div>
            <span class="header-meta">${snapshotAge}</span>
        </div>
    `;

    if (hasWarning) {
        const warning = document.createElement("div");
        warning.className = "warning-pill";
        warning.textContent = server.warnings[0];
        header.querySelector(".server-identity").appendChild(warning);
    }

    card.appendChild(header);

    const deviceGrid = document.createElement("div");
    deviceGrid.className = "device-grid";

    if (!hasNoDeviceData) {
        devices.forEach((device) => {
            const deviceDiv = document.createElement("div");
            deviceDiv.className = "device-item";

            const memPercent = percent(device.memory_used_mb, device.memory_total_mb);
            const utilPercent = clampPercent(device.utilization_percent ?? 0);
            const usedGb = formatMemoryGb(device.memory_used_mb);
            const totalGb = formatMemoryGb(device.memory_total_mb);
            const memoryLabel = `${usedGb}/${totalGb} GB`;

            const deviceHeader = document.createElement("div");
            deviceHeader.className = "device-header";
            const deviceName = String(device.display_name || device.name || "");
            deviceHeader.innerHTML = `
                <span class="device-name" title="${escapeHtml(deviceName)}">#${device.index} ${escapeHtml(deviceName)}</span>
            `;
            deviceDiv.appendChild(deviceHeader);

            const bars = document.createElement("div");
            bars.className = "device-bars";
            bars.innerHTML = `
                <div class="bar-row">
                    <div class="bar memory"><span style="width: ${memPercent}%"></span></div>
                    <span class="bar-label">${memoryLabel}</span>
                </div>
                <div class="bar-row">
                    <div class="bar utilization"><span style="width: ${utilPercent}%"></span></div>
                    <span class="bar-label">Util ${utilPercent}%</span>
                </div>
            `;
            deviceDiv.appendChild(bars);

            if (device.processes && device.processes.length) {
                const processDiv = document.createElement("div");
                processDiv.className = "device-processes";
                processDiv.innerHTML = renderPrimaryProcess(device.processes);
                deviceDiv.appendChild(processDiv);
            }

            deviceGrid.appendChild(deviceDiv);
        });
    } else if (!hasWarning) {
        const empty = document.createElement("div");
        empty.className = "device-empty";
        empty.textContent = "No device data";
        deviceGrid.appendChild(empty);
    }

    if (!isCompact) {
        card.appendChild(deviceGrid);
    }
}

function renderRefreshStatus(message, variant = "") {
    refreshStatus.textContent = message;
    refreshStatus.className = `refresh-status${variant ? ` ${variant}` : ""}`;
}

async function start() {
    try {
        const pollInterval = await loadConfig();
        refreshButton.addEventListener("click", () => loadAll(true));
        loadAll();
        setInterval(() => loadAll(), pollInterval);
    } catch (error) {
        renderRefreshStatus(`Error: ${error.message}`, "error");
    }
}

start();

// Keep Node imports treating this browser script as an ES module.
export {};
