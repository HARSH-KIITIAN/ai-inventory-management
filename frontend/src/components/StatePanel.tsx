import type { WorldState } from "../types";
import { Bar, Section } from "./ui";

export function StatePanel({ state }: { state: WorldState }) {
  const node = state.nodes[0];
  const budget = state.budgets[0];
  const forecast = state.forecasts[0];

  return (
    <div className="state-panel">
      <Section title="Node & Constraints">
        {node && (
          <Bar label={`Storage · ${node.id}`} used={node.occupied_l} total={node.storage_capacity_l} unit="L" />
        )}
        {budget && (
          <Bar label={`Budget · ${budget.period}`} used={budget.committed} total={budget.amount} unit="$" />
        )}
      </Section>

      {forecast && (
        <Section title="Demand Signal">
          <div className="kv">
            <div><span>Forecast</span><b>{forecast.forecast_daily}/day</b></div>
            <div>
              <span>Recent actual</span>
              <b className={forecast.recent_daily > forecast.forecast_daily * 1.5 ? "hot" : ""}>
                {forecast.recent_daily ?? "—"}/day
              </b>
            </div>
            <div><span>Review period</span><b>{forecast.review_period_days}d</b></div>
            <div><span>Safety stock</span><b>{forecast.safety_stock_days}d</b></div>
          </div>
        </Section>
      )}

      <Section title="Inventory">
        <table className="tbl">
          <thead><tr><th>SKU</th><th>Node</th><th className="num">On hand</th></tr></thead>
          <tbody>
            {state.inventory.map((i, idx) => (
              <tr key={idx}><td>{i.sku}</td><td>{i.node_id}</td><td className="num">{i.on_hand}</td></tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section title="Open Purchase Orders">
        {state.purchase_orders.filter((p) => p.status !== "cancelled").length === 0 ? (
          <p className="muted">None</p>
        ) : (
          <table className="tbl">
            <thead>
              <tr><th>PO</th><th>Supplier</th><th className="num">Ord</th><th className="num">Conf</th><th className="num">Out</th><th>Status</th></tr>
            </thead>
            <tbody>
              {state.purchase_orders.filter((p) => p.status !== "cancelled").map((p) => (
                <tr key={p.id}>
                  <td>{p.id}</td>
                  <td>{p.supplier_id}</td>
                  <td className="num">{p.qty_ordered}</td>
                  <td className="num">{p.qty_confirmed ?? "—"}</td>
                  <td className="num">{p.outstanding}</td>
                  <td><span className={`pill pill-${p.status}`}>{p.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title="Suppliers">
        <table className="tbl">
          <thead>
            <tr><th>Supplier</th><th className="num">Price</th><th className="num">Lead</th><th className="num">MOQ</th><th className="num">Pack</th><th className="num">Rel.</th></tr>
          </thead>
          <tbody>
            {state.offers.map((o, idx) => {
              const name = state.suppliers.find((s) => s.id === o.supplier_id)?.name ?? o.supplier_id;
              return (
                <tr key={idx}>
                  <td title={name}>{o.supplier_id}</td>
                  <td className="num">${o.unit_price}</td>
                  <td className="num">{o.lead_time_days}d</td>
                  <td className="num">{o.moq}</td>
                  <td className="num">{o.pack_size}</td>
                  <td className="num">{(o.reliability * 100).toFixed(0)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Section>
    </div>
  );
}
