// modules/api.js
export async function fetchStats(server, channelName, type) {
    try {
        const response = await fetch(`http://${server}:5000/stats/live/${channelName}/${type}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`Error fetching ${type} stats:`, error);
        return [];
    }
}

export async function fetchChannelConfig(server) {
    try {
        const response = await fetch(`http://${server}:5000/api/channels`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        return data.channels || [];
    } catch (error) {
        console.error('Error fetching channel config:', error);
        return [];
    }
}

export async function fetchServerList() {
    try {
        const response = await fetch('servers.json');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        return Array.isArray(data.servers) ? data.servers : [];
    } catch (error) {
        console.error('Error fetching server list:', error);
        return [];
    }
}