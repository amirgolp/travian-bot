import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { ActiveAccountProvider } from "./components/ActiveAccountContext";
import Overview from "./pages/Overview";
import Accounts from "./pages/Accounts";
import Villages from "./pages/Villages";
import VillageDetail from "./pages/VillageDetail";
import Farmlists from "./pages/Farmlists";
import FarmlistDetail from "./pages/FarmlistDetail";
import BuildQueue from "./pages/BuildQueue";
import Hero from "./pages/Hero";
import MapTiles from "./pages/MapTiles";
import Reports from "./pages/Reports";

export default function App() {
  return (
    <ActiveAccountProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Overview />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="villages" element={<Villages />} />
          <Route path="villages/:id" element={<VillageDetail />} />
          <Route path="farmlists" element={<Farmlists />} />
          <Route path="farmlists/:id" element={<FarmlistDetail />} />
          <Route path="build" element={<BuildQueue />} />
          <Route path="hero" element={<Hero />} />
          <Route path="map" element={<MapTiles />} />
          <Route path="reports" element={<Reports />} />
        </Route>
      </Routes>
    </ActiveAccountProvider>
  );
}
