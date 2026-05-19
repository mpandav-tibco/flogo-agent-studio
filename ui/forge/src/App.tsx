import { BrowserRouter, Route, Routes } from "react-router-dom";
import Gallery from "./pages/Gallery";
import Editor from "./pages/Editor";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Gallery />} />
        <Route path="/agents/new" element={<Editor />} />
        <Route path="/agents/:id" element={<Editor />} />
      </Routes>
    </BrowserRouter>
  );
}
