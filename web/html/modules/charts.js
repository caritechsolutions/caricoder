import { channelState, STATE_EVENTS, INPUT_TYPES } from './state.js';
import { fetchStats } from './api.js';

const CHART_COLORS = {
    input: 'rgb(75, 192, 192)',
    outputs: [
        'rgb(255, 99, 132)',
        'rgb(54, 162, 235)',
        'rgb(255, 206, 86)',
        'rgb(153, 102, 255)',
        'rgb(255, 159, 64)'
    ]
};

const COMMON_CHART_OPTIONS = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    scales: {
        x: {
            type: 'time',
            time: {
                unit: 'second',
                displayFormats: {
                    second: 'HH:mm:ss'
                }
            },
            ticks: {
                maxRotation: 0
            }
        },
        y: {
            beginAtZero: true,
            title: {
                display: true,
                text: 'Mbps'
            }
        }
    },
    plugins: {
        tooltip: {
            mode: 'nearest',
            intersect: false
        }
    }
};

export function initializeCharts() {
    const inputCtx = document.getElementById('input-traffic-chart').getContext('2d');
    const outputCtx = document.getElementById('output-traffic-chart').getContext('2d');

    // Input chart
    channelState.setChart('input', new Chart(inputCtx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'Input Bitrate',
                data: [],
                borderColor: CHART_COLORS.input,
                tension: 0.1,
                borderWidth: 1.5
            }]
        },
        options: {
            ...COMMON_CHART_OPTIONS,
            plugins: {
                ...COMMON_CHART_OPTIONS.plugins,
                title: {
                    display: true,
                    text: 'Input Traffic'
                }
            }
        }
    }));

    // Output chart
    channelState.setChart('output', new Chart(outputCtx, {
        type: 'line',
        data: {
            datasets: []
        },
        options: {
            ...COMMON_CHART_OPTIONS,
            plugins: {
                ...COMMON_CHART_OPTIONS.plugins,
                title: {
                    display: true,
                    text: 'Output Traffic'
                }
            }
        }
    }));
}

export function setupOutputDatasets() {
    if (!channelState.config?.outputs) return;

    const datasets = channelState.config.outputs.map((output, index) => ({
        label: `Output ${index + 1} (${output.type})`,
        data: [],
        borderColor: CHART_COLORS.outputs[index % CHART_COLORS.outputs.length],
        tension: 0.1,
        borderWidth: 1.5
    }));

    channelState.charts.output.data.datasets = datasets;
    channelState.charts.output.update('none');
}


function formatInputData(inputData) {
    if (!inputData || inputData.length === 0) {
        return [];
    }

    // Assume the first item in the array has the expected properties
    const firstItem = inputData[0];

    // Check the available properties and use the most appropriate one for the 'y' value
    let yProp;
    if (firstItem.stats?.['bandwidth-mbps']) {
        yProp = 'bandwidth-mbps';
    } else if (firstItem.stats?.bitrate_mbps) {
        yProp = 'bitrate_mbps';
    } else {
        console.error('No valid bitrate property found in input data');
        return [];
    }

    return inputData.map(item => ({
        x: new Date(item.timestamp * 1000),
        y: parseFloat(item.stats[yProp] || 0)
    }));
}

export async function updateCharts() {
    if (!channelState.server || !channelState.name || !channelState.config) return;

    try {
        // Update input chart
        const inputType = channelState.getInputType();
        // console.log('Input type:', inputType); // Debug log
        
        if (!inputType) {
            console.error('Unknown input type');
            return;
        }

        const inputData = await fetchStats(channelState.server, channelState.name, inputType);
        // console.log('Input data:', inputData); // Debug log

        const formattedInputData = formatInputData(inputData);

        if (formattedInputData.length > 0) {
            channelState.charts.input.data.datasets[0].data = formattedInputData;
            channelState.charts.input.update();
        }


        // Update output charts
        const outputs = channelState.config.outputs || [];
        await Promise.all(outputs.map(async (_, index) => {
            const outputData = await fetchStats(channelState.server, channelState.name, `udp_output_${index}`);
            
            if (outputData?.length > 0) {
                const formattedData = outputData.map(item => ({
                    x: new Date(item.timestamp * 1000),
                    y: parseFloat(item.stats.bitrate_mbps || 0)
                }));

                if (channelState.charts.output.data.datasets[index]) {
                    channelState.charts.output.data.datasets[index].data = formattedData;
                }
            }
        }));

        channelState.charts.output.update('none');

    } catch (error) {
        console.error('Error updating charts:', error);
    }
}

export function startChartUpdates() {
    if (channelState.updateInterval) {
        clearInterval(channelState.updateInterval);
    }
    updateCharts();
    channelState.updateInterval = setInterval(updateCharts, 1000);
}

export function stopChartUpdates() {
    if (channelState.updateInterval) {
        clearInterval(channelState.updateInterval);
        channelState.updateInterval = null;
    }
}

// Event Listeners
channelState.addEventListener(STATE_EVENTS.CONFIG_LOADED, setupOutputDatasets);