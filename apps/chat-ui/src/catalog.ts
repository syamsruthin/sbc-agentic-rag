// Filter values for the plan picker, taken from the 2027 portal catalog in the facts bundle. They are catalog
// metadata, not SBC text, and only narrow the search: the API applies each bot's plan scope in code.

/** States with active Individual-market plans: the Plan Shopper's choices. */
export const SHOPPER_STATES: Record<string, string> = {
  CA: "California",
  CO: "Colorado",
  CT: "Connecticut",
  FL: "Florida",
  GA: "Georgia",
  IN: "Indiana",
  KY: "Kentucky",
  MD: "Maryland",
  ME: "Maine",
  MO: "Missouri",
  NH: "New Hampshire",
  NV: "Nevada",
  OH: "Ohio",
  TX: "Texas",
  VA: "Virginia",
  WA: "Washington",
  WI: "Wisconsin",
};

/** Every state in the catalog (the broker searches all markets, including New York's group plans). */
export const BROKER_STATES = [...Object.keys(SHOPPER_STATES), "NY"].sort();

export const MARKETS = ["Individual", "Small Group", "Large Group", "ABF", "MEWA"];

export const METAL_TIERS = ["Bronze", "Silver", "Gold", "Platinum", "Catastrophic"];

export const PLAN_TYPES = [
  "HMO",
  "HMO + HSA",
  "HMO + HRA",
  "PPO",
  "PPO + HSA",
  "PPO + HRA",
  "PPO + HIA Plus",
  "EPO",
  "EPO + HSA",
  "EPO + HRA",
  "POS",
  "POS + HSA",
  "POS + HRA",
];
