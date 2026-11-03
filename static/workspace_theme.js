(function () {
  const themes = {
    autoxpert: { sidebar: '#eee15b', button: '#3937ad', active: '#4b5059', activeText: '#ffffff' },
    'trece-uno': { sidebar: '#4642b4', button: '#eee15b', active: '#353195', activeText: '#ffffff' },
    neutral: { sidebar: '#42586b', button: '#486581', active: '#536c80', activeText: '#ffffff' }
  };
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
      ['--workspace-sidebar-text', '#ffffff'],
      ['--workspace-button', colors.button],
      ['--workspace-button-text', '#ffffff'],
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
