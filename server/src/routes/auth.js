import { Router } from "express";
import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import User from "../models/User.js";
import { requireAuth } from "../middleware/auth.js";

const router = Router();
const publicUser = user => ({ id: user._id, nickname: user.nickname, email: user.email || "", robloxUsername: user.robloxUsername || "", robloxUserId: user.robloxUserId || "" });
const issueToken = user => jwt.sign({ sub: user._id.toString() }, process.env.JWT_SECRET, { expiresIn: "7d" });

router.post("/register", async (req, res) => {
  try {
    const { nickname, password, email = "", robloxUsername = "" } = req.body || {};
    if (!/^[a-zA-Z0-9_]{3,24}$/.test(nickname || "")) return res.status(400).json({ message: "Никнейм: 3–24 символа, латиница, цифры и _." });
    if (typeof password !== "string" || password.length < 8 || password.length > 72) return res.status(400).json({ message: "Пароль должен содержать от 8 до 72 символов." });
    if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return res.status(400).json({ message: "Проверьте электронную почту." });
    if (await User.exists({ nickname: nickname.trim() })) return res.status(409).json({ message: "Этот никнейм уже занят." });
    const passwordHash = await bcrypt.hash(password, 12);
    const user = await User.create({ nickname: nickname.trim(), passwordHash, email, robloxUsername });
    res.status(201).json({ token: issueToken(user), user: publicUser(user) });
  } catch (e) { res.status(500).json({ message: "Не удалось создать аккаунт." }); }
});

router.post("/login", async (req, res) => {
  const { nickname, password } = req.body || {};
  const user = await User.findOne({ nickname: String(nickname || "").trim() }).select("+passwordHash");
  if (!user || !(await bcrypt.compare(String(password || ""), user.passwordHash))) return res.status(401).json({ message: "Неверный никнейм или пароль." });
  res.json({ token: issueToken(user), user: publicUser(user) });
});

router.get("/me", requireAuth, async (req, res) => {
  const user = await User.findById(req.userId);
  if (!user) return res.status(404).json({ message: "Аккаунт не найден." });
  res.json({ user: publicUser(user) });
});

router.patch("/profile", requireAuth, async (req, res) => {
  const { email = "", robloxUsername = "" } = req.body || {};
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return res.status(400).json({ message: "Проверьте электронную почту." });
  if (typeof robloxUsername !== "string" || robloxUsername.length > 20) return res.status(400).json({ message: "Некорректный Roblox Username." });
  const user = await User.findById(req.userId);
  if (!user) return res.status(404).json({ message: "Аккаунт не найден." });
  user.email = email.trim();
  user.robloxUsername = robloxUsername.trim();
  user.robloxUserId = "";
  if (user.robloxUsername) {
    try {
      const response = await fetch("https://users.roblox.com/v1/usernames/users", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({usernames:[user.robloxUsername], excludeBannedUsers:true}) });
      const data = await response.json();
      if (data.data?.[0]) { user.robloxUsername = data.data[0].name; user.robloxUserId = String(data.data[0].id); }
      else return res.status(400).json({ message: "Пользователь Roblox не найден." });
    } catch { return res.status(502).json({ message: "Не удалось проверить Roblox Username." }); }
  }
  await user.save();
  res.json({ user: publicUser(user) });
});

export default router;
