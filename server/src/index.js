import "dotenv/config";
import express from "express";
import mongoose from "mongoose";
import cors from "cors";
import helmet from "helmet";
import rateLimit from "express-rate-limit";
import authRoutes from "./routes/auth.js";
import robloxRoutes from "./routes/roblox.js";

const app = express();
app.set("trust proxy", 1);
app.use(helmet());
app.use(cors({ origin: process.env.CLIENT_ORIGIN?.split(",") || true }));
app.use(express.json({ limit:"32kb" }));
app.use("/api", rateLimit({ windowMs:15*60*1000, limit:300, standardHeaders:true, legacyHeaders:false }));
app.get("/health", (_req,res)=>res.json({ok:true,service:"my-subscribers-api",database:mongoose.connection.readyState===1?"connected":"disconnected"}));
app.use("/api/auth", authRoutes);
app.use("/api/roblox", robloxRoutes);
app.use((err,_req,res,_next)=>{ console.error(err); res.status(500).json({message:"Внутренняя ошибка сервера."}); });

const port = process.env.PORT || 10000;
if (!process.env.MONGODB_URI || !process.env.JWT_SECRET) {
  console.error("MONGODB_URI and JWT_SECRET are required"); process.exit(1);
}
await mongoose.connect(process.env.MONGODB_URI);
app.listen(port, "0.0.0.0", ()=>console.log(`My Subscribers API listening on ${port}`));
