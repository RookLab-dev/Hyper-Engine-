import { Link, Route, Routes } from "react-router-dom";
import { Analysis } from "@/pages/Analysis";
import { MonteCarlo } from "@/pages/MonteCarlo";
import { Overview } from "@/pages/Overview";
import { Performance } from "@/pages/Performance";
import { Risk } from "@/pages/Risk";
import { Sessions } from "@/pages/Sessions";
import { Trades } from "@/pages/Trades";

export function App() {
  return (
    <div className="shell">
      <nav className="nav">
        <h1>Hyper-Engine</h1>
        <Link to="/">Overview</Link>
        <Link to="/sessions">Sessions</Link>
        <Link to="/trades">Trades</Link>
        <Link to="/performance">Performance</Link>
        <Link to="/analysis">Analysis</Link>
        <Link to="/monte-carlo">Monte Carlo</Link>
        <Link to="/risk">Risk</Link>
        <p className="muted small">Read-only dashboard. Sessions with <code>ended_at: null</code> are open rows, not proof the bot is running.</p>
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/trades" element={<Trades />} />
          <Route path="/performance" element={<Performance />} />
          <Route path="/analysis" element={<Analysis />} />
          <Route path="/monte-carlo" element={<MonteCarlo />} />
          <Route path="/risk" element={<Risk />} />
          <Route path="*" element={<p>Not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}