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
        
        const systemInstruction = `You are a Global 3D Geospatial Intelligence Assistant. 
        Analyze the user's request. If they want to build, simulate, or find a building, estimate realistic dimensions in meters.
        Return ONLY a valid JSON object:
        {
            "center": [latitude, longitude],
            "description": "Professional English explanation of the simulation and location",
            "building": {
                "length": number,
                "width": number,
                "height": number
            }
        }
        Example: if user asks 'Build a 300m skyscraper in London', return {"center": [51.5074, -0.1278], "description": "Simulating a 300-meter skyscraper in Central London.", "building": {"length": 50, "width": 50, "height": 300}}`;

        const model = genAI.getGenerativeModel({ model: "gemini-1.5-flash" });
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