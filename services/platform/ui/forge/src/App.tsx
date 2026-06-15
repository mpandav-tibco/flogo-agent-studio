import { BrowserRouter, Route, Routes } from "react-router-dom";
import Gallery from "./pages/Gallery";
import Editor from "./pages/Editor";
import Admin from "./pages/Admin";
import KnowledgeBase from "./pages/KnowledgeBase";
import { ThemeProvider } from "./contexts/ThemeContext";

export default function App() {
  return (
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Gallery />} />
          <Route path="/agents/new" element={<Editor />} />
          <Route path="/agents/:id" element={<Editor />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/kb" element={<KnowledgeBase />} />
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  );
}
