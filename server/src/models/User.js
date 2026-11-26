import mongoose from "mongoose";

const userSchema = new mongoose.Schema({
  nickname: { type: String, required: true, unique: true, trim: true, minlength: 3, maxlength: 24 },
  passwordHash: { type: String, required: true, select: false },
  email: { type: String, default: "", trim: true, lowercase: true },
  robloxUsername: { type: String, default: "", trim: true },
  robloxUserId: { type: String, default: "" }
}, { timestamps: true });

export default mongoose.model("User", userSchema);
