import Cookies from 'js-cookie';

// Cookie name from the server-rendered meta tag; falls back to Django's default.
const csrfCookieName = () =>
  document.querySelector('meta[name="csrf-cookie-name"]')?.content || 'csrftoken';

export const getCsrfToken = () => Cookies.get(csrfCookieName());
