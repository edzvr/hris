(function () {
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
  function applyWorkspaceTheme(customColor) {
    const themeName = document.body.dataset.companyTheme;
    const theme = themes[themeName] || themes.neutral;
    if (customColor !== undefined && customColor !== null && !/^#[0-9a-f]{6}$/i.test(customColor)) {
      throw new Error('Invalid workspace accent color');
    }
    const colors = customColor
      ? { ...theme, sidebar: customColor, button: customColor }
      : theme;
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
  const defaultTheme = themes[companyTheme] || themes.neutral;
  window.applyWorkspaceTheme = applyWorkspaceTheme;
  window.applyWorkspaceAccent = applyWorkspaceTheme;
  window.workspaceThemeButtonColor = () => defaultTheme.button;
  const settingsKey = document.body.classList.contains('hris-admin-sidebar-page')
    ? 'hrisAdminSettings' : 'hrisStaffDashboardSettings';
  let savedColor = null;
  try {
    const settings = JSON.parse(localStorage.getItem(settingsKey) || '{}');
    savedColor = settings && settings.buttonColor;
    if (savedColor && !/^#[0-9a-f]{6}$/i.test(savedColor)) {
      throw new Error('Invalid saved workspace accent color');
    }
  } catch (error) {
    console.error('Could not restore saved workspace color:', error);
    savedColor = null;
  }
  const customColor = savedColor
    && !['#2563eb', defaultTheme.button.toLowerCase()].includes(savedColor.toLowerCase())
    ? savedColor : null;
  applyWorkspaceTheme(customColor);
  function bindColorPreview() {
    const picker = document.getElementById('buttonColor') || document.getElementById('staffButtonColor');
    if (picker) picker.addEventListener('input', () => applyWorkspaceTheme(picker.value));
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindColorPreview);
  } else {
    bindColorPreview();
  }
})();
