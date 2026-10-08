document.addEventListener('alpine:init', () => {
  // Single naira formatter: Alpine surfaces call it as $money(value).
  Alpine.magic('money', () => (value) =>
    '₦' + Number(value || 0).toLocaleString('en-NG', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  );
});
