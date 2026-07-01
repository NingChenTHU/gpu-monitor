const serverGrid = typeof document === "undefined" ? null : document.querySelector("#server-grid");

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

export function formatGpuName(name) {
    const normalized = String(name || "").toUpperCase();
    const match = normalized.match(/(A\d{3,4}|\d{4})/);
    if (!match) {
        return name || "GPU";
    }
    return match[1];
}

export function renderPrimaryProcess(processes) {
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

async function loadAll() {
    renderStatus("Loading...");
    try {
        const response = await fetch("/api/servers");
        if (!response.ok) {
            throw new Error(`/api/servers responded with ${response.status}`);
        }
        const servers = await response.json();
        renderServers(servers);
    } catch (error) {
        console.error(error);
        renderStatus(`Error: ${error.message}`, "error");
    }
}

async function loadConfig() {
    const response = await fetch("/api/config");
    if (!response.ok) {
        throw new Error(`/api/config responded with ${response.status}`);
    }
    const config = await response.json();
    const pollIntervalSeconds = Number(config.poll_interval_seconds);
    if (!Number.isFinite(pollIntervalSeconds) || pollIntervalSeconds <= 0) {
        throw new Error("Invalid poll interval");
    }
    return pollIntervalSeconds * 1000;
}

function renderServers(servers) {
    serverGrid.innerHTML = "";
    if (!servers.length) {
        serverGrid.innerHTML = '<p class="empty">No servers configured yet.</p>';
        return;
    }
    const sortedServers = [...servers].sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));

    sortedServers.forEach((server) => {
        const hasWarning = Boolean(server.warnings && server.warnings.length);
        const hasNoGpuData = !server.gpus.length;
        const isCompact = hasWarning && hasNoGpuData;
        const lastSeen = formatLastSeen(server.last_seen);
        const snapshotAge = server.is_stale ? `Stale · ${lastSeen}` : `Updated ${lastSeen}`;
        const card = document.createElement("div");
        card.className = `server-card${server.is_stale ? " stale" : ""}${isCompact ? " compact" : ""}`;

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

        const gpuGrid = document.createElement("div");
        gpuGrid.className = "gpu-grid";

        if (!hasNoGpuData) {
            server.gpus.forEach((gpu) => {
                const gpuDiv = document.createElement("div");
                gpuDiv.className = "gpu-item";

                const memPercent = percent(gpu.memory_used_mb, gpu.memory_total_mb);
                const utilPercent = clampPercent(gpu.utilization_percent ?? 0);
                const usedGb = formatMemoryGb(gpu.memory_used_mb);
                const totalGb = formatMemoryGb(gpu.memory_total_mb);
                const memoryLabel = `${usedGb}/${totalGb} GB`;

                const gpuHeader = document.createElement("div");
                gpuHeader.className = "gpu-header";
                gpuHeader.innerHTML = `
                    <span class="gpu-name">#${gpu.index} ${escapeHtml(formatGpuName(gpu.name))}</span>
                `;
                gpuDiv.appendChild(gpuHeader);

                const bars = document.createElement("div");
                bars.className = "gpu-bars";
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
                gpuDiv.appendChild(bars);

                if (gpu.processes && gpu.processes.length) {
                    const processDiv = document.createElement("div");
                    processDiv.className = "gpu-processes";
                    processDiv.innerHTML = renderPrimaryProcess(gpu.processes);
                    gpuDiv.appendChild(processDiv);
                }

                gpuGrid.appendChild(gpuDiv);
            });
        } else if (!hasWarning) {
            const empty = document.createElement("div");
            empty.className = "gpu-empty";
            empty.textContent = "No GPU data";
            gpuGrid.appendChild(empty);
        }

        if (!isCompact) {
            card.appendChild(gpuGrid);
        }
        serverGrid.appendChild(card);
    });
}

function renderStatus(message, variant = "") {
    serverGrid.innerHTML = `<p class="empty ${variant}">${escapeHtml(message)}</p>`;
}

async function start() {
    renderStatus("Loading...");
    try {
        const pollInterval = await loadConfig();
        await loadAll();
        setInterval(loadAll, pollInterval);
    } catch (error) {
        console.error(error);
        renderStatus(`Error: ${error.message}`, "error");
    }
}

if (serverGrid) {
    start();
}


