const express = require('express');
const { GoogleGenerativeAI } = require("@google/generative-ai");
const path = require('path');
require('dotenv').config();

const app = express();
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY);

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

app.post('/api/map-prompt', async (req, res) => {
    try {
        const { prompt } = req.body;

        const systemInstruction = `You are a Malaysian Urban Planning AI Assistant.
You MUST return ONLY a raw JSON object. No markdown. No code blocks. No backticks. No explanation. No extra text before or after. Just the JSON object starting with { and ending with }.

RULES:
1. If user is navigating/flying/going/showing/zooming to a place with NO build request → "isNavigation": true
2. If user wants to BUILD or SIMULATE → "isNavigation": false, fill all fields
3. Coordinates must be within Malaysia: lat 0.8 to 7.4, lng 99.5 to 119.5
4. Extract building name from prompt. If none given, create a realistic Malaysian name.
5. Building type and color mapping:
   mall → "#F5F5F5"
   retail_store → "#FF4444"
   government_building → "#FF8C00"
   office_tower → "#FFD700"
   office_area → "#90EE90"
   hotel → "#00FFFF"
   hospital → "#4169E1"
   lrt_station → "#9B59B6"
   bus_terminal → "#FF69B4"
   airport → "#FFF8DC"
   warehouse → "#708090"
   logistics_hub → "#CD853F"
   restaurant → "#FF7F50"
   theme_park → "#32CD32"
   mixed_use → "#FF00FF"
   other → "#00FFFF"

JSON format (return exactly this structure):
{"isNavigation":false,"center":[latitude,longitude],"buildingName":"Name","buildingType":"type","color":"#hex","description":"One sentence.","building":{"length":number,"width":number,"height":number}}

For navigation only:
{"isNavigation":true,"center":[latitude,longitude],"buildingName":"Place Name","description":"Navigating to Place Name."}`;

        const model = genAI.getGenerativeModel({
            model: "gemini-2.5-flash",
            generationConfig: {
                responseMimeType: "application/json"
            }
        });

        const result = await model.generateContent(systemInstruction + "\nUser prompt: " + prompt);
        const response = await result.response;
        let text = response.text().trim();

        // Belt-and-suspenders: strip any accidental markdown fences
        text = text.replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '').trim();

        // Extract JSON object if there's any surrounding text
        const jsonMatch = text.match(/\{[\s\S]*\}/);
        if (!jsonMatch) {
            console.error("No JSON found in response:", text);
            throw new Error("AI did not return valid JSON");
        }

        const parsed = JSON.parse(jsonMatch[0]);

        // Validate required fields
        if (!parsed.hasOwnProperty('isNavigation') || !parsed.center) {
            throw new Error("AI response missing required fields");
        }

        // Clamp coordinates to Malaysia bounds just in case
        parsed.center[0] = Math.min(Math.max(parsed.center[0], 0.8), 7.4);
        parsed.center[1] = Math.min(Math.max(parsed.center[1], 99.5), 119.5);

        console.log(`✓ [${parsed.isNavigation ? 'NAV' : 'BUILD'}] ${parsed.buildingName ?? parsed.description}`);
        res.json(parsed);

    } catch (error) {
        console.error("AI Error:", error.message ?? error);
        res.status(500).json({ error: "Failed to process AI request: " + (error.message ?? "Unknown error") });
    }
});

const PORT = 3000;
app.listen(PORT, () => console.log(`System active at http://localhost:${PORT}`));