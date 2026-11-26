import { Router } from "express";
const router = Router();
router.get("/resolve/:username", async (req, res) => {
  try {
    const response = await fetch("https://users.roblox.com/v1/usernames/users", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({usernames:[req.params.username], excludeBannedUsers:true}) });
    if (!response.ok) return res.status(502).json({ message:"Roblox API временно недоступен." });
    const data = await response.json();
    const user = data.data?.[0];
    if (!user) return res.status(404).json({ message:"Roblox пользователь не найден." });
    const thumb = await fetch(`https://thumbnails.roblox.com/v1/users/avatar?userIds=${user.id}&size=420x420&format=Png&isCircular=false`);
    const thumbData = await thumb.json();
    res.json({ id:user.id, name:user.name, displayName:user.displayName, thumbnail:thumbData.data?.[0]?.imageUrl || null });
  } catch { res.status(502).json({ message:"Не удалось связаться с Roblox." }); }
});
export default router;
