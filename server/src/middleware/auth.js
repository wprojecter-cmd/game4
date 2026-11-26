import jwt from "jsonwebtoken";

export function requireAuth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  try {
    if (!token) return res.status(401).json({ message: "Требуется авторизация" });
    req.userId = jwt.verify(token, process.env.JWT_SECRET).sub;
    next();
  } catch {
    return res.status(401).json({ message: "Сессия истекла. Войдите снова." });
  }
}
