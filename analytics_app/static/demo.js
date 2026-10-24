'use strict';
const $ = selector => document.querySelector(selector);
let consent = true;
try { consent = localStorage.getItem('gather:consent') !== 'no'; } catch (_) {}
const analytics = new GatherAnalytics({ enabled: consent });
$('#tracking').checked = consent;
$('#tracking').addEventListener('change', () => {
  analytics.setEnabled($('#tracking').checked);
  try { localStorage.setItem('gather:consent', $('#tracking').checked ? 'yes' : 'no'); } catch (_) {}
});
const cart = new Map();
let menu = [];
let pendingOrder = null;
let submitting = false;
const money = cents => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(cents / 100);
function renderCart() {
  $('#cart-items').replaceChildren();
  let total = 0, count = 0;
  for (const [id, quantity] of cart) {
    const item = menu.find(m => m.id === id); total += item.price_cents * quantity; count += quantity;
    const row = document.createElement('div'); row.className = 'cart-row';
    const title = document.createElement('span'); title.textContent = `${quantity} × ${item.name}`;
    const remove = document.createElement('button'); remove.className = 'remove'; remove.textContent = '−'; remove.disabled = submitting || Boolean(pendingOrder);
    remove.setAttribute('aria-label', `Remove one ${item.name}`);
    remove.addEventListener('click', () => { if (quantity === 1) cart.delete(id); else cart.set(id, quantity - 1); analytics.track('cart_removed', { item_id: id }); renderCart(); });
    row.append(title, remove); $('#cart-items').append(row);
  }
  if (!cart.size) $('#cart-items').textContent = 'A good meal starts with a favorite. Add something from the menu.';
  $('#total').textContent = money(total); $('#cart-count').textContent = `${count} items`;
  $('#checkout').disabled = !cart.size || submitting;
  document.querySelectorAll('.add-item').forEach(b => { b.disabled = submitting || Boolean(pendingOrder); });
}
$('#checkout').addEventListener('click', async () => {
  if (submitting || !cart.size) return;
  submitting = true;
  if (!pendingOrder) {
    pendingOrder = { key: crypto.randomUUID(), body: { anonymous_id: analytics.anonymousId, session_id: analytics.sessionId,
      items: [...cart].map(([item_id, quantity]) => ({ item_id, quantity })) } };
    analytics.track('checkout_started', { item_count: [...cart.values()].reduce((a, b) => a + b, 0) });
  }
  renderCart(); $('#checkout').textContent = 'Sending to the kitchen…';
  $('#order-status').textContent = '';
  try {
    const response = await fetch('/api/v1/orders', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': pendingOrder.key }, body: JSON.stringify(pendingOrder.body), signal: AbortSignal.timeout(10000) });
    if (!response.ok) {
      if (response.status >= 400 && response.status < 500 && response.status !== 429) pendingOrder = null;
      throw new Error(`Order could not be confirmed (HTTP ${response.status}). Please retry.`);
    }
    const result = await response.json();
    $('#order-status').textContent = `You're on the list! Order ${result.order_id.slice(0, 8)} confirmed · ${money(result.total_cents)}. See the server event in your dashboard.`;
    pendingOrder = null; cart.clear(); analytics.track('order_confirmation_viewed', { order_id: result.order_id }); analytics.flush();
  } catch (error) {
    $('#order-status').textContent = `${error.message} Retrying uses the same order key.`;
  } finally { submitting = false; $('#checkout').textContent = pendingOrder ? 'Retry order →' : 'Place demo order →'; renderCart(); }
});
async function loadMenu() {
  try {
    const response = await fetch('/api/v1/menu');
    if (!response.ok) throw new Error('The menu could not be loaded.');
    menu = await response.json();
    for (const item of menu) {
      const card = document.createElement('article'); card.className = `menu-card dish-${item.id}`;
      const visual = document.createElement('div'); visual.className = 'dish-visual'; visual.textContent = item.icon; visual.setAttribute('aria-hidden', 'true');
      const body = document.createElement('div'); body.className = 'dish-body';
      const category = document.createElement('small'); category.textContent = item.category;
      const name = document.createElement('h3'); name.textContent = item.name;
      const description = document.createElement('p'); description.textContent = item.description;
      const bottom = document.createElement('div'); bottom.className = 'dish-bottom';
      const price = document.createElement('strong'); price.textContent = money(item.price_cents);
      const add = document.createElement('button'); add.className = 'button small add-item'; add.textContent = '+ Add'; add.setAttribute('aria-label', `Add ${item.name}`);
      add.addEventListener('click', () => {
        if (submitting || pendingOrder) return;
        if ((cart.get(item.id) || 0) >= 20) { $('#order-status').textContent = 'Maximum 20 of each dish per order.'; return; }
        cart.set(item.id, (cart.get(item.id) || 0) + 1);
        analytics.track('cart_added', { item_id: item.id, price_cents: item.price_cents }); renderCart();
      });
      bottom.append(price, add); body.append(category, name, description, bottom); card.append(visual, body); $('#menu-items').append(card);
    }
    analytics.track('menu_viewed', { item_count: menu.length }); analytics.flush(); renderCart();
  } catch (error) { $('#menu-items').textContent = `${error.message} Reload the page to try again.`; }
}
renderCart(); loadMenu();
