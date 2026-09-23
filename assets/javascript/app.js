import * as JsCookie from 'js-cookie';
import { getCsrfToken } from './csrf';

export { getCsrfToken };
export const Cookies = JsCookie.default;

if (typeof window.SiteJS === 'undefined') {
  window.SiteJS = {};
}

window.SiteJS.app = {
  Cookies: JsCookie.default,
  getCsrfToken,
};
