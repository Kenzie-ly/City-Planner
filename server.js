const express = require('express');
const { GoogleGenerativeAI } = require("@google/generative-ai");
const path = require('path');
require('dotenv').config();

const app = express();
// Note: Ensure your .env file has GEMINI_API_KEY
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY);

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

app.post('/api/map-prompt', async (req, res) => {
    try {
        const { prompt } = req.body;

        const systemInstruction = `You are a Malaysian Geospatial Intelligence Assistant. 
        You ONLY handle locations within Malaysia (Peninsular Malaysia, Sabah, and Sarawak).
        Analyze the user's request. If they want to build, simulate, or find a building, estimate realistic dimensions in meters.
        If the user asks about a location outside Malaysia, still return a valid JSON but set center to Kuala Lumpur and note it in the description.
        Return ONLY a valid JSON object with no markdown formatting:
        {
            "center": [latitude, longitude],
            "description": "Professional English explanation of the simulation and location",
            "building": {
                "length": number,
                "width": number,
                "height": number
            }
        }
        Example: if user asks 'Build a 10-story hospital in Kuala Lumpur', return {"center": [3.1390, 101.6869], "description": "Simulating a 10-story hospital in central Kuala Lumpur, estimated at 40 meters height.", "building": {"length": 80, "width": 60, "height": 40}}`;

        // FIX: Use gemini-2.0-flash (the correct available model)
        const model = genAI.getGenerativeModel({ model: "gemini-2.5-flash" });
        const result = await model.generateContent(systemInstruction + "\nUser: " + prompt);
        const response = await result.response;

        let text = response.text().replace(/```json|```/g, "").trim();
        res.json(JSON.parse(text));

    } catch (error) {
        console.error("AI Error:", error);
        res.status(500).json({ error: "Failed to process AI request" });
    }
});

const PORT = 3000;
app.listen(PORT, () => console.log(`System active at http://localhost:${PORT}`));