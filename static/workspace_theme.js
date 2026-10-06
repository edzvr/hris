(function () {
  const themes = {
    autoxpert: { sidebar: '#eee15b', sidebarText: '#30384d', button: '#3937ad', buttonText: '#ffffff', active: '#4b5059', activeText: '#ffffff' },
    'trece-uno': { sidebar: '#4642b4', sidebarText: '#ffffff', button: '#eee15b', buttonText: '#30384d', active: '#353195', activeText: '#ffffff' },
    neutral: { sidebar: '#42586b', sidebarText: '#f5f7fa', button: '#486581', buttonText: '#ffffff', active: '#536c80', activeText: '#ffffff' }
  };
  function contrastText(color) {
    const rgb = [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16) / 255);
    const linear = rgb.map(value => value <= 0.04045 ? value / 12.92 : Math.pow((value + 0.055) / 1.055, 2.4));
    const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
    return 1.05 / (luminance + 0.05) >= 4.5 ? '#ffffff' : '#243247';
  }
  function applyWorkspaceTheme(customColor) {
    const themeName = document.body.dataset.companyTheme;
    const theme = themes[themeName] || themes.neutral;
    if (customColor !== undefined && customColor !== null && !/^#[0-9a-f]{6}$/i.test(customColor)) {
      throw new Error('Invalid workspace accent color');
    }
    const colors = customColor
      ? { ...theme, sidebar: customColor, sidebarText: contrastText(customColor), button: customColor, buttonText: contrastText(customColor) }
      : theme;
    for (const [variable, color] of [
      ['--workspace-sidebar', colors.sidebar],
      ['--workspace-sidebar-text', colors.sidebarText],
      ['--workspace-button', colors.button],
      ['--workspace-button-text', colors.buttonText],
      ['--workspace-nav-active', colors.active],
      ['--workspace-nav-active-text', colors.activeText]
    ]) document.body.style.setProperty(variable, color);
  }
  const companyTheme = document.body.dataset.companyTheme;
  const defaultTheme = themes[companyTheme] || themes.neutral;
  window.applyWorkspaceTheme = applyWorkspaceTheme;
  window.applyWorkspaceAccent = applyWorkspaceTheme;
  window.workspaceThemeButtonColor = () => defaultTheme.button;
})();
