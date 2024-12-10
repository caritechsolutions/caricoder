// 1. Function to log messages
function log(message) {
    console.log(`[${new Date().toISOString()}] ${message}`);
}

// 2. Function to fetch data from the API
async function fetchResourceData(resourceType, serverAddress) {
    log(`Fetching data for resource: ${resourceType} from server: ${serverAddress}`);
    try {
        const response = await fetch(`http://${serverAddress}:5000/metrics/live/${resourceType}`);
        const data = await response.json();
        log(`Successfully fetched data for ${resourceType}`);
        return data;
    } catch (error) {
        log(`Error fetching data for ${resourceType}: ${error.message}`);
        return resourceType === 'network' ? {} : [];
    }
}

// 3. Function to update charts with new data
async function updateCharts() {
    log('Starting chart update');
    const serverSelect = document.getElementById('server-select');
    const serverAddress = serverSelect.value;

    if (!serverAddress) {
        log('No server selected. Skipping update.');
        return;
    }

    const resources = ['cpu', 'memory', 'hdd', 'gpu'];

    for (const resource of resources) {
        log(`Updating chart for ${resource}`);
        try {
            const data = await fetchResourceData(resource, serverAddress);
            const chart = Chart.getChart(`${resource}-live-chart`);
            
            if (chart && data.length > 0) {
                const chartData = data.map(item => ({
                    x: new Date(item.timestamp * 1000),
                    y: parseFloat(item.value)
                }));

                chart.data.datasets[0].data = chartData;
                
                // Update the card value with the earliest (leftmost) data point
                const earliestDataPoint = chartData[0];
                updateCardValue(resource, earliestDataPoint.y);
                
                // Maintain consistent y-axis scaling
                chart.options.scales.y.min = 0;
                chart.options.scales.y.max = 100;
                
                // Adjust x-axis to show last 5 minutes of data
                const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000);
                chart.options.scales.x.min = fiveMinutesAgo;
                chart.options.scales.x.max = new Date();
                
                chart.update();
                log(`Successfully updated chart for ${resource}`);
            } else {
                log(`Chart not found or no data for ${resource}`);
            }
        } catch (error) {
            log(`Error updating chart for ${resource}: ${error.message}`);
        }
    }

    // Update network charts
    try {
        const networkData = await fetchResourceData('network', serverAddress);
        updateNetworkCharts(networkData);
    } catch (error) {
        log(`Error updating network charts: ${error.message}`);
    }

    await updateChannelStatus(serverAddress);
    log('Finished updating all charts');
}

// 4. Function to update network charts
function updateNetworkCharts(data) {
    const networkContainer = document.getElementById('network-charts-container');
    const networkCardsContainer = document.getElementById('network-cards-container');

    // Filter out the loopback interface
    const interfaces = Object.keys(data).filter(iface => iface !== 'lo');

    // Create or update network cards and charts
    interfaces.forEach(iface => {
        // Create or update network card
        let card = document.getElementById(`${iface}-network-card`);
        if (!card) {
            card = document.createElement('div');
            card.id = `${iface}-network-card`;
            card.className = 'stat-card';
            card.innerHTML = `
                <h3>${iface} Network Usage</h3>
                <div class="stat-value"></div>
                <div class="chart-container">
                    <canvas id="${iface}-live-chart"></canvas>
                </div>
            `;
            networkCardsContainer.appendChild(card);
            
            // Add click event listener for pop-out
            card.addEventListener('click', () => handleChartPopout(`${iface}-network`));
        }

        let chart = Chart.getChart(`${iface}-live-chart`);
        if (!chart) {
            const ctx = document.getElementById(`${iface}-live-chart`).getContext('2d');
            chart = new Chart(ctx, {
                type: 'line',
                data: {
                    datasets: [
                        {
                            label: `${iface} (Receive)`,
                            borderColor: 'rgb(75, 192, 192)',
                            data: [],
                            fill: false
                        },
                        {
                            label: `${iface} (Send)`,
                            borderColor: 'rgb(255, 99, 132)',
                            data: [],
                            fill: false
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            type: 'time',
                            time: {
                                unit: 'minute',
                                displayFormats: {
                                    minute: 'HH:mm:ss'
                                }
                            },
                            title: {
                                display: true,
                                text: 'Time'
                            }
                        },
                        y: {
                            beginAtZero: true,
                            title: {
                                display: true,
                                text: 'bits/s'
                            },
                            ticks: {
                                callback: function(value) {
                                    return formatBits(value) + '/s';
                                }
                            }
                        }
                    },
                    plugins: {
                        legend: {
                            display: true
                        },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    let label = context.dataset.label || '';
                                    if (label) {
                                        label += ': ';
                                    }
                                    label += formatBits(context.parsed.y) + '/s';
                                    return label;
                                }
                            }
                        }
                    },
                    animation: {
                        duration: 0 // Disable animations to prevent blinking
                    }
                }
            });
        }

        // Update chart data
        const receiveData = data[iface].map(item => ({
            x: new Date(item.timestamp * 1000),
            y: item.value.recv_rate * 8 // Convert to bits
        }));
        const sendData = data[iface].map(item => ({
            x: new Date(item.timestamp * 1000),
            y: item.value.send_rate * 8 // Convert to bits
        }));

        chart.data.datasets[0].data = receiveData;
        chart.data.datasets[1].data = sendData;

        // Auto-scale the y-axis
        const allValues = receiveData.concat(sendData).map(item => item.y);
        if (allValues.length > 0) {
            const minValue = Math.min(...allValues);
            const maxValue = Math.max(...allValues);
            const padding = (maxValue - minValue) * 0.1;
            chart.options.scales.y.min = Math.max(0, minValue - padding);
            chart.options.scales.y.max = maxValue + padding;
        }

        // Adjust x-axis to show last 5 minutes of data
        const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000);
        chart.options.scales.x.min = fiveMinutesAgo;
        chart.options.scales.x.max = new Date();

        chart.update();

        // Update card value with total network usage
        const latestData = data[iface][0].value;
        const totalUsage = (latestData.recv_rate + latestData.send_rate) * 8; // Convert to bits
        updateCardValue(`${iface}-network`, totalUsage);
    });

    // Remove charts and cards for interfaces that no longer exist
    const existingCards = networkCardsContainer.querySelectorAll('.stat-card');
    existingCards.forEach(card => {
        const iface = card.id.replace('-network-card', '');
        if (!interfaces.includes(iface)) {
            card.remove();
            const chart = Chart.getChart(`${iface}-live-chart`);
            if (chart) {
                chart.destroy();
            }
        }
    });
}

// New function to format bits
function formatBits(bits) {
    if (bits === 0) return '0 bps';
    const k = 1000;
    const sizes = ['bps', 'Kbps', 'Mbps', 'Gbps', 'Tbps'];
    const i = Math.floor(Math.log(bits) / Math.log(k));
    return parseFloat((bits / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function updateCardValue(resource, value) {
    const cardValue = document.querySelector(`#${resource}-card .stat-value`);
    log(`Updating Card value for ${resource} with ${value}`);
    if (cardValue) {
        if (resource.includes('network')) {
            cardValue.textContent = value !== undefined ? `${formatBits(value)}` : 'N/A';
        } else {
            cardValue.textContent = value !== undefined ? `${value.toFixed(2)}%` : 'N/A';
        }
    } else {
        log(`Card value element not found for ${resource}`);
    }
}

// 7. Function to initialize charts
function initializeCharts() {
    const resources = ['cpu', 'memory', 'hdd', 'gpu'];
    const colors = {
        cpu: 'rgb(255, 99, 132)',
        memory: 'rgb(54, 162, 235)',
        hdd: 'rgb(75, 192, 192)',
        gpu: 'rgb(153, 102, 255)'
    };

    resources.forEach(resource => {
        const ctx = document.getElementById(`${resource}-live-chart`);
        if (ctx) {
            const commonOptions = {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    x: {
                        type: 'time',
                        time: {
                            unit: 'minute',
                            displayFormats: {
                                minute: 'HH:mm:ss'
                            }
                        },
                        title: {
                            display: true,
                            text: 'Time'
                        }
                    },
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Percentage'
                        },
                        ticks: {
                            callback: function(value, index, values) {
                                return value.toFixed(2) + '%';
                            }
                        },
                        min: 0,
                        max: 100,
                        suggestedMin: 0,
                        suggestedMax: 100
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    },
                    tooltip: {
                        callbacks: {
                            label: function(context) {
                                let label = context.dataset.label || '';
                                if (label) {
                                    label += ': ';
                                }
                                label += context.parsed.y.toFixed(2) + '%';
                                return label;
                            }
                        }
                    }
                }
            };

            new Chart(ctx, {
                type: 'line',
                data: {
                    datasets: [{
                        label: resource.toUpperCase(),
                        data: [],
                        borderColor: colors[resource],
                        tension: 0.1,
                        pointRadius: 0,
                        fill: false
                    }]
                },
                options: commonOptions
            });
        } else {
            log(`Canvas element not found for ${resource}`);
        }
    });

    // Network charts will be initialized dynamically when data is received
}

// 8. Function to handle chart pop-out
function handleChartPopout(resource) {
    log(`Popping out chart for ${resource}`);
    const overlay = document.querySelector('.expanded-overlay');
    const expandedChart = document.querySelector('.expanded-chart');
    const expandedCanvas = document.getElementById('expanded-canvas');
    const expandedTitle = document.querySelector('.expanded-chart-title');

    overlay.style.display = 'block';
    expandedChart.style.display = 'block';
    expandedTitle.textContent = `${resource.toUpperCase()} Usage`;

    const isNetworkChart = resource.includes('network');
    let originalChart;

    if (isNetworkChart) {
        const iface = resource.split('-')[0];
        originalChart = Chart.getChart(`${iface}-live-chart`);
    } else {
        originalChart = Chart.getChart(`${resource}-live-chart`);
    }

    if (!originalChart) {
        log(`Original chart not found for ${resource}`);
        return;
    }

    const popoutChartConfig = {
        type: 'line',
        data: JSON.parse(JSON.stringify(originalChart.data)),
        options: JSON.parse(JSON.stringify(originalChart.options))
    };

    // Ensure consistent y-axis formatting for network charts
    if (isNetworkChart) {
        popoutChartConfig.options.scales.y = {
            beginAtZero: true,
            title: {
                display: true,
                text: 'bits/s'
            },
            ticks: {
                callback: function(value) {
                    return formatBits(value) + '/s';
                }
            }
        };
    }

    const popoutChart = new Chart(expandedCanvas, popoutChartConfig);

    // Immediately update the chart with data
    updatePopoutChart(popoutChart, resource);

    // Update the popout chart every 5 seconds
    const updateInterval = setInterval(() => {
        updatePopoutChart(popoutChart, resource);
    }, 5000);

    // Close button functionality
    const closeButton = document.querySelector('.close-button');
    closeButton.onclick = () => {
        clearInterval(updateInterval);
        overlay.style.display = 'none';
        expandedChart.style.display = 'none';
        popoutChart.destroy();
    };

    // Click outside to close
    overlay.onclick = (event) => {
        if (event.target === overlay) {
            closeButton.onclick();
        }
    };
}

function updatePopoutChart(chart, resource) {
    const serverSelect = document.getElementById('server-select');
    const serverAddress = serverSelect.value;

    if (!serverAddress) {
        log('No server selected. Skipping popout chart update.');
        return;
    }

    log(`Updating popout chart for ${resource}`);

    const isNetworkChart = resource.includes('network');
    const iface = isNetworkChart ? resource.split('-')[0] : null;

    fetchResourceData(isNetworkChart ? 'network' : resource, serverAddress)
        .then(data => {
            log(`Fetched data for ${resource}:`, JSON.stringify(data));

            if (isNetworkChart) {
                if (data && data[iface]) {
                    log(`Network data for ${iface}:`, JSON.stringify(data[iface]));
                    const receiveData = data[iface].map(item => ({
                        x: new Date(item.timestamp * 1000),
                        y: item.value.recv_rate * 8 // Convert to bits
                    }));
                    const sendData = data[iface].map(item => ({
                        x: new Date(item.timestamp * 1000),
                        y: item.value.send_rate * 8 // Convert to bits
                    }));
                    chart.data.datasets[0].data = receiveData;
                    chart.data.datasets[1].data = sendData;

                    const allValues = receiveData.concat(sendData).map(item => item.y);
                    if (allValues.length > 0) {
                        const minValue = Math.min(...allValues);
                        const maxValue = Math.max(...allValues);
                        const padding = (maxValue - minValue) * 0.1;
                        chart.options.scales.y.min = Math.max(0, minValue - padding);
                        chart.options.scales.y.max = maxValue + padding;
                    }
                } else {
                    log(`No data found for network interface ${iface}`);
                }
            } else {
                if (Array.isArray(data) && data.length > 0) {
                    const chartData = data.map(item => ({
                        x: new Date(item.timestamp * 1000),
                        y: parseFloat(item.value)
                    }));
                    chart.data.datasets[0].data = chartData;
                    
                    if (resource !== 'hdd') {
                        const values = chartData.map(item => item.y).filter(v => !isNaN(v));
                        if (values.length > 0) {
                            const minValue = Math.min(...values);
                            const maxValue = Math.max(...values);
                            const padding = (maxValue - minValue) * 0.1;
                            chart.options.scales.y.min = Math.max(0, minValue - padding);
                            chart.options.scales.y.max = maxValue + padding;
                        }
                    }
                } else {
                    log(`No data or invalid data structure for resource ${resource}`);
                }
            }
            
            // Adjust x-axis to show last 5 minutes of data
            const fiveMinutesAgo = new Date(Date.now() - 5 * 60 * 1000);
            chart.options.scales.x.min = fiveMinutesAgo;
            chart.options.scales.x.max = new Date();
            
            log(`Chart data before update:`, JSON.stringify(chart.data));
            chart.update();
            log(`Updated popout chart for ${resource}`);
        })
        .catch(error => {
            log(`Error updating popout chart for ${resource}: ${error.message}`);
        });
}

// 10. Function to fetch and display channel status
async function updateChannelStatus(serverAddress) {
    log('Fetching channel status');
    try {
        const response = await fetch(`http://${serverAddress}:5000/metrics/latest`);
        const data = await response.json();

        const statusList = document.getElementById('channel-status-list');
        if (statusList) {
            statusList.innerHTML = '';
            
            if (data.channels) {
                const [running, total] = data.channels.value.split('/');
                const listItem = document.createElement('li');
                listItem.textContent = `Running Channels: ${running}/${total}`;
                statusList.appendChild(listItem);
            }
            log('Successfully updated channel status');
        } else {
            log('Channel status list element not found');
        }
    } catch (error) {
        log(`Error updating channel status: ${error.message}`);
    }
}

// 11. Function to populate server select dropdown
function populateServerSelect() {
    const serverSelect = document.getElementById('server-select');
    fetch('servers.json')
        .then(response => response.json())
        .then(data => {
            data.servers.forEach((server, index) => {
                const option = document.createElement('option');
                option.value = server;
                option.textContent = server;
                if (index === 0) {
                    option.selected = true;
                }
                serverSelect.appendChild(option);
            });
            // Trigger initial update after populating the dropdown
            updateCharts();
        })
        .catch(error => log(`Error loading server list: ${error.message}`));
}

// 12. Event listener for server select change
document.getElementById('server-select').addEventListener('change', () => {
    updateCharts();
});

// 13. Initialize the dashboard
function initializeDashboard() {
    populateServerSelect();
    initializeCharts();

    // Update charts and channel status every 5 seconds
    setInterval(() => {
        updateCharts();
    }, 5000);

    // Add click event listeners for chart pop-out
    const resources = ['cpu', 'memory', 'hdd', 'gpu'];
    resources.forEach(resource => {
        const chart = document.querySelector(`#${resource}-card .chart-container`);
        if (chart) {
            chart.addEventListener('click', () => handleChartPopout(resource));
        }
    });

    // Add event listener for network charts (which are created dynamically)
    const networkContainer = document.getElementById('network-charts-container');
    networkContainer.addEventListener('click', (event) => {
        const chartContainer = event.target.closest('.chart-container');
        if (chartContainer) {
            const chartId = chartContainer.querySelector('canvas').id;
            const resource = chartId.replace('-live-chart', '');
            handleChartPopout(resource);
        }
    });
}

// 14. Call initializeDashboard when the DOM is fully loaded
document.addEventListener('DOMContentLoaded', initializeDashboard);

