export interface Scenario {
  id: string;
  title: string;
  situation: string;
  recommended_sku: string | null;
  recommended_node: string | null;
  recommended_qty: number | null;
  focus_po_id: string | null;
}

export interface ProposedAction {
  action: "create_po" | "modify_po" | "cancel_po" | "escalate" | "none";
  sku: string | null;
  node_id: string | null;
  supplier_id: string | null;
  qty: number | null;
  po_id: string | null;
  rationale: string;
}

export interface Decision {
  category: "accept" | "modify" | "reject" | "investigate" | "escalate";
  summary: string;
  reasoning: string;
  key_factors: string[];
  proposed_action: ProposedAction;
  confidence: number;
  requires_approval: boolean;
}

export interface ValidationReport {
  ok: boolean;
  violations: string[];
  warnings: string[];
  metrics: Record<string, unknown>;
}

export interface ExecutionResult {
  executed: boolean;
  action: ProposedAction;
  po_id: string | null;
  message: string;
  post_validation: ValidationReport | null;
  outcome_ok: boolean;
}

export interface TraceEvent {
  type: string;
  [key: string]: unknown;
}

export interface WorldState {
  scenario: Scenario;
  products: any[];
  nodes: any[];
  suppliers: any[];
  offers: any[];
  inventory: any[];
  forecasts: any[];
  purchase_orders: any[];
  budgets: any[];
}
