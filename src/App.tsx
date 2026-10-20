import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DetailsPage, HomePage, SearchPage, WatchPage } from "./pages";

export default function App() {
  return <BrowserRouter><Routes>
    <Route path="/" element={<HomePage />} />
    <Route path="/search" element={<SearchPage />} />
    <Route path="/movie/:id" element={<DetailsPage expectedType="Movie" />} />
    <Route path="/series/:id" element={<DetailsPage expectedType="Series" />} />
    <Route path="/watch/:id" element={<WatchPage />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></BrowserRouter>;
}
