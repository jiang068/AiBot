document.addEventListener('DOMContentLoaded', () => {
    setTimeout(() => {
        MinbotUI.prototype.updateConnectionStatus = function(status, type) {
            const textEl = document.getElementById('connectionStatus');
            const dotEl = document.getElementById('connectionStatusDot');
            if (textEl) textEl.textContent = status;
            if (dotEl) {
                if (type === 'success') dotEl.className = 'status-dot bg-connected';
                else if (type === 'danger') dotEl.className = 'status-dot bg-disconnected';
                else dotEl.className = 'status-dot bg-secondary';
            }
        };
    }, 100);
});
