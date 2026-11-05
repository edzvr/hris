(function () {
  const isAdmin = document.body.classList.contains('hris-admin-sidebar-page');
  const adminTheme = { sidebar: '#2563eb', button: '#2563eb', active: '#eef0f3', activeText: '#374151' };
  let adminTopbarColor = adminTheme.sidebar;
  const themes = {
    autoxpert: { sidebar: '#eee15b', button: '#3937ad', active: '#4b5059', activeText: '#ffffff' },
    'trece-uno': { sidebar: '#4642b4', button: '#eee15b', active: '#353195', activeText: '#ffffff' },
    neutral: { sidebar: '#42586b', button: '#486581', active: '#536c80', activeText: '#ffffff' }
  };
  function readableText(background) {
    const linear = [1, 3, 5].map(index => {
      const channel = parseInt(background.slice(index, index + 2), 16) / 255;
      return channel <= 0.04045 ? channel / 12.92 : Math.pow((channel + 0.055) / 1.055, 2.4);
    });
    const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    return 1.05 / (luminance + 0.05) >= 4.5 ? '#ffffff' : '#000000';
  }
  function applyWorkspaceTheme(customColor, topbarColor) {
    const themeName = document.body.dataset.companyTheme;
    const theme = isAdmin ? adminTheme : themes[themeName] || themes.neutral;
    if (customColor !== undefined && customColor !== null && !/^#[0-9a-f]{6}$/i.test(customColor)) {
      throw new Error('Invalid workspace accent color');
    }
    if (isAdmin && topbarColor !== undefined) {
      if (topbarColor !== null && !/^#[0-9a-f]{6}$/i.test(topbarColor)) {
        throw new Error('Invalid Admin top bar color');
      }
      adminTopbarColor = topbarColor || adminTheme.sidebar;
    }
    const colors = customColor
      ? { ...theme, sidebar: isAdmin ? adminTopbarColor : customColor, button: customColor }
      : { ...theme, sidebar: isAdmin ? adminTopbarColor : theme.sidebar };
    for (const [variable, color] of [
      ['--workspace-sidebar', colors.sidebar],
      ['--workspace-sidebar-text', readableText(colors.sidebar)],
      ['--workspace-button', colors.button],
      ['--workspace-button-text', readableText(colors.button)],
      ['--workspace-nav-active', colors.active],
      ['--workspace-nav-active-text', colors.activeText]
    ]) document.body.style.setProperty(variable, color);
  }
  const companyTheme = document.body.dataset.companyTheme;
  const defaultTheme = isAdmin ? adminTheme : themes[companyTheme] || themes.neutral;
  window.applyWorkspaceTheme = applyWorkspaceTheme;
  window.applyWorkspaceAccent = applyWorkspaceTheme;
  window.workspaceThemeButtonColor = () => defaultTheme.button;
  window.workspaceThemeTopbarColor = () => adminTheme.sidebar;
  const settingsKey = document.body.classList.contains('hris-admin-sidebar-page')
    ? 'hrisAdminSettings' : 'hrisStaffDashboardSettings';
  let savedColor = null;
  try {
    const settings = JSON.parse(localStorage.getItem(settingsKey) || '{}');
    if (isAdmin && settings && settings.topbarColor) {
      if (!/^#[0-9a-f]{6}$/i.test(settings.topbarColor)) {
        throw new Error('Invalid saved Admin top bar color');
      }
      adminTopbarColor = settings.topbarColor;
    }
    savedColor = settings && settings.buttonColor;
    if (savedColor && !/^#[0-9a-f]{6}$/i.test(savedColor)) {
      throw new Error('Invalid saved workspace accent color');
    }
  } catch (error) {
    console.error('Could not restore saved workspace color:', error);
    savedColor = null;
    adminTopbarColor = adminTheme.sidebar;
  }
  const customColor = savedColor
    && !['#2563eb', defaultTheme.button.toLowerCase()].includes(savedColor.toLowerCase())
    ? savedColor : null;
  applyWorkspaceTheme(customColor);
  function bindColorPreview() {
    const picker = document.getElementById('buttonColor') || document.getElementById('staffButtonColor');
    if (picker) {
      ['input', 'change'].forEach(event => picker.addEventListener(
        event, () => applyWorkspaceTheme(picker.value)
      ));
    }
    const topbarPicker = document.getElementById('topbarColor');
    if (isAdmin && topbarPicker) {
      ['input', 'change'].forEach(event => topbarPicker.addEventListener(
        event, () => applyWorkspaceTheme(picker ? picker.value : null, topbarPicker.value)
      ));
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindColorPreview);
  } else {
    bindColorPreview();
  }
})();
