import {getCsrfToken} from './csrf';

// data-logout-link turns the GET into a POST to skip allauth's logout confirmation.
document.addEventListener('DOMContentLoaded', () => {
  const links = document.querySelectorAll('a[data-logout-link]');
  links.forEach((link) => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const form = document.createElement('form');
      form.method = 'POST';
      form.action = link.getAttribute('href');

      const csrf = document.createElement('input');
      csrf.type = 'hidden';
      csrf.name = 'csrfmiddlewaretoken';
      csrf.value = getCsrfToken();
      form.appendChild(csrf);

      document.body.appendChild(form);
      form.submit();
    });
  });
});
