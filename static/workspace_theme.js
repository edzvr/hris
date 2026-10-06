(function () {
  function applyWorkspaceAccent(color) {
    if (!/^#[0-9a-f]{6}$/i.test(color)) throw new Error('Invalid workspace accent color');
    const rgb = [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16) / 255);
    const linear = rgb.map(value => value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4));
    const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    const text = 1.05 / (luminance + 0.05) >= 4.5 ? '#ffffff' : '#000000';
    document.documentElement.style.setProperty('--workspace-accent', color);
    document.documentElement.style.setProperty('--workspace-accent-text', text);
  }
  window.applyWorkspaceAccent = applyWorkspaceAccent;
  const key = document.body.classList.contains('hris-admin-sidebar-page') ? 'hrisAdminSettings' : 'hrisStaffDashboardSettings';
  function restoreAccent() {
    const settings = JSON.parse(localStorage.getItem(key) || '{}');
    applyWorkspaceAccent(settings.buttonColor || '#2563eb');
  }
  restoreAccent();
  window.addEventListener('storage', event => { if (event.key === key) restoreAccent(); });
})();
