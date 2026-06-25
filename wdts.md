# The Mathematics of the Weighted Temporal Degradation Score (WDTS)

The **Weighted Temporal Degradation Score (WDTS)** is the core priority scheduling algorithm used by ARIA to select the next optimal target for an improvement cycle. It evaluates the system dynamically, considering not only instantaneous fitness but also evolutionary neglect (Opportunity-Weighted Stagnation).

The composite score $\text{WDTS}(\tau)$ for any given tool $\tau$ is bounded strictly between $0.0$ and $1.0$, where higher scores represent higher urgency for introspection.

## 1. The Core Equation

The WDTS is an affine combination of five orthogonal components, each weighted by a fixed constant $\omega_i$:

$$
\text{WDTS}(\tau) = \sum_{i=1}^{5} \omega_i \cdot \mathcal{C}_i(\tau)
$$

Where the weights $\omega$ are defined as:
- $\omega_{\text{health}} = 0.30$ (Current Health)
- $\omega_{\text{traj}} = 0.25$ (Trajectory)
- $\omega_{\text{resist}} = 0.20$ (Fix Resistance)
- $\omega_{\text{impact}} = 0.15$ (System Impact)
- $\omega_{\text{ows}} = 0.10$ (Opportunity-Weighted Stagnation)

All components $\mathcal{C}_i(\tau) \in [0, 1]$.

---

## 2. The Components

### 2.1 Current Health ($\mathcal{C}_{\text{health}}$)
Evaluates the immediate operational fitness of the tool.

$$
\mathcal{C}_{\text{health}}(\tau) = 0.50(1 - P_{\text{pass}}) + 0.25 \min\left(\frac{L_{90}}{10.0}, 1\right) + 0.15 \min\left(\frac{M_{\text{avg}}}{512.0}, 1\right) + 0.10 \min\left(\frac{T_{\text{avg}}}{4000}, 1\right)
$$

Where:
- $P_{\text{pass}} \in [0, 1]$ is the success rate.
- $L_{90}$ is the 90th percentile latency (seconds).
- $M_{\text{avg}}$ is the average memory consumption (MB).
- $T_{\text{avg}}$ is the average token consumption per execution.

### 2.2 Trajectory ($\mathcal{C}_{\text{traj}}$)
Measures recent performance degradation using discrete windows.

$$
\Delta_{\text{degrade}} = P_{\text{pass}}([t_{-20}, t_{-10}]) - P_{\text{pass}}([t_{-10}, t_0])
$$
$$
\mathcal{C}_{\text{traj}}(\tau) = \max\left( \min(2 \cdot \Delta_{\text{degrade}}, 1), 0 \right)
$$

### 2.3 Fix Resistance ($\mathcal{C}_{\text{resist}}$)
Measures the historical difficulty the Evolutionary Engine has had in improving the tool.

Let $A_{\text{fail}}$ be the number of rejected/failed candidates, and $A_{\text{success}}$ be the number of successful deployments. Let $A_{\text{total}} = A_{\text{fail}} + A_{\text{success}}$.

$$
\rho(\tau) = \frac{A_{\text{fail}}}{A_{\text{total}}}
$$
$$
\mathcal{C}_{\text{resist}}(\tau) = \min\left(\frac{\ln(1 + A_{\text{fail}})}{\ln(21)}, 1\right) \cdot \rho(\tau)
$$

*Circuit Breaker Condition:* If $A_{\text{fail}} > 10$ and $A_{\text{success}} = 0$, the resistance score is artificially halved ($\mathcal{C}_{\text{resist}} \gets 0.5 \cdot \mathcal{C}_{\text{resist}}$) to prevent priority starvation caused by intractable tools.

### 2.4 System Impact ($\mathcal{C}_{\text{impact}}$)
Ranks tools by their actual usage frequency relative to the entire ecosystem.

$$
\mathcal{C}_{\text{impact}}(\tau) = \min\left(3 \cdot \frac{E(\tau)}{\sum_{j \in \text{Tools}} E(j)}, 1\right)
$$
Where $E(\tau)$ is the total historical executions of tool $\tau$.

---

## 3. Component 5: Opportunity-Weighted Stagnation (OWS)

The OWS replaces archaic wall-clock recency with a "Subjective Timeline" driven by evolutionary neglect. It is composed of a non-linear opportunity metric combined with a Hybrid Cycle-Time stagnation function.

$$
\mathcal{C}_{\text{ows}}(\tau) = 0.6 \cdot \mathcal{O}(\tau) + 0.4 \cdot \mathcal{S}_{\text{total}}(\tau)
$$

### 3.1 Opportunity ($\mathcal{O}$)
Opportunity models the potential margin for improvement. A highly degraded tool offers exponentially higher improvement ROI.

$$
\mathcal{O}(\tau) = (1 - P_{\text{pass}}(\tau))^2
$$

### 3.2 Total Stagnation ($\mathcal{S}_{\text{total}}$)
Stagnation is an affine combination of cycle-based neglect (Bypasses) and temporal neglect.

$$
\mathcal{S}_{\text{total}}(\tau) = 0.7 \cdot \mathcal{S}_{\text{bypass}}(\tau) + 0.3 \cdot \mathcal{S}_{\text{time}}(\tau)
$$

#### 3.2.1 Bypass Stagnation ($\mathcal{S}_{\text{bypass}}$)
This metric solves the "Monoculture Exploit." A tool only accumulates a bypass count ($B_{\tau}$) if its $\text{WDTS} \ge 0.20$ but it loses the priority bid to a different tool.

$$
\mathcal{S}_{\text{bypass}}(\tau) = 1 - e^{-\frac{B_{\tau}}{\lambda_{\text{bypass}}}}
$$

Where the decay constant $\lambda_{\text{bypass}}$ dynamically shrinks based on the tool's historical difficulty multiplier ($D_{\tau}$). If a tool repeatedly fails sandbox tests, $D_{\tau}$ increases, accelerating the stagnation curve:

$$
\lambda_{\text{bypass}} = \frac{20.0}{D_{\tau}}
$$

#### 3.2.2 Time Stagnation ($`\mathcal{S}_{\text{time}}`$)
Standard exponential decay driven by wall-clock hours ($`H_{\tau}`$) since the last improvement attempt.

```math
\mathcal{S}_{\text{time}}(\tau) = 1 - e^{-\frac{H_{\tau}}{\lambda_{\text{time}}}}
```

Where $`\lambda_{\text{time}} = 48.0`$.

---

## 4. The State Machine of the Subjective Timeline
The parameters governing OWS ($`B_{\tau}, H_{\tau}, D_{\tau}`$) act as a state machine inside `aria.db`.

**State Transitions on Cycle Outcome:**

For a given tool $`\tau`$ targeted for improvement:

- **On Success (Deployment):**
```math
B_{\tau} \gets 0 \quad ; \quad H_{\tau} \gets 0 \quad ; \quad D_{\tau} \gets 1.0
```

- **On Failure (Sandbox/Validator Rejection):**
```math
B_{\tau} \gets 0 \quad ; \quad H_{\tau} \gets 0 \quad ; \quad D_{\tau} \gets D_{\tau} + 0.5
```

- **On Bypass (Priority Bid Lost while WDTS $`\ge 0.20`$):**
```math
B_{\tau} \gets B_{\tau} + 1
```