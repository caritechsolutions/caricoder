// modules/player.js

import { channelState, STATE_EVENTS } from './state.js';

class VideoPlayer {
    constructor() {
        this.video = document.getElementById('channel-video');
        this.refreshBtn = document.getElementById('refresh-feed');
        this.hls = null;
        this.initialized = false;
    }

    initialize() {
        if (!this.video) {
            console.error('Video element not found');
            return;
        }

        // Initialize HLS if supported
        if (Hls.isSupported()) {
            this.hls = new Hls({
                maxBufferLength: 30,
                maxMaxBufferLength: 60,
                enableWorker: true

                
            });
            
            this.hls.attachMedia(this.video);
            
            // Event listeners - only add if refresh button exists
            if (this.refreshBtn) {
                this.refreshBtn.addEventListener('click', () => {
                    this.loadFeed();
                });
            }
            
            // Error handling
            this.hls.on(Hls.Events.ERROR, (event, data) => {
                console.error('HLS Error:', data);
                if (data.fatal) {
                    switch (data.type) {
                        case Hls.ErrorTypes.NETWORK_ERROR:
                            this.handleNetworkError();
                            break;
                        case Hls.ErrorTypes.MEDIA_ERROR:
                            this.handleMediaError();
                            break;
                        default:
                            this.handleFatalError();
                            break;
                    }
                }
            });

            this.initialized = true;

            // Only load if we have the necessary state
            if (channelState.server && channelState.name) {
                this.loadFeed();
            }
        } else if (this.video.canPlayType('application/vnd.apple.mpegurl')) {
            // Fallback to native HLS support (Safari)
            this.initialized = true;
            if (channelState.server && channelState.name) {
                this.loadFeed();
            }
        } else {
            console.error('HLS is not supported in this browser');
        }
    }

    loadFeed() {
        if (!this.initialized) {
            console.warn('Player not initialized yet');
            return;
        }

        if (!channelState.server || !channelState.name) {
            console.warn('Channel state not ready yet');
            return;
        }

        const feedUrl = `http://${channelState.server}/content/${channelState.name}/playlist.m3u8?help`;
        console.log('Loading feed:', feedUrl);
        
        if (this.hls) {
            this.hls.loadSource(feedUrl);
        } else {
            this.video.src = feedUrl;
        }
    }

    handleNetworkError() {
        console.log('Attempting to recover from network error...');
        setTimeout(() => this.loadFeed(), 2000);
    }

    handleMediaError() {
        console.log('Attempting to recover from media error...');
        this.hls.recoverMediaError();
    }

    handleFatalError() {
        console.log('Fatal error occurred, reinitializing player...');
        if (this.hls) {
            this.hls.destroy();
            this.initialize();
        }
    }

    destroy() {
        if (this.hls) {
            this.hls.destroy();
            this.hls = null;
        }
        this.initialized = false;
    }
}

// Create and export player instance
const player = new VideoPlayer();

// Listen for state changes
channelState.addEventListener(STATE_EVENTS.CONFIG_LOADED, () => {
    if (player.initialized) {
        player.loadFeed();
    }
});

// Initialize player when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    player.initialize();
});

// Handle visibility changes
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && player.initialized) {
        player.loadFeed();
    }
});

// Export player instance
export { player };