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
5. Building type and color mapping (these are hologram display colors, make them vivid and visible):
   mall → "#00BFFF"
   retail_store → "#FF4444"
   government_building → "#FF8C00"
   office_tower → "#FFD700"
   office_area → "#90EE90"
   hotel → "#00FFFF"
   hospital → "#4169E1"
   lrt_station → "#9B59B6"
   bus_terminal → "#FF69B4"
   airport → "#F0A500"
   warehouse → "#708090"
   logistics_hub → "#CD853F"
   restaurant → "#FF7F50"
   theme_park → "#32CD32"
   mixed_use → "#FF00FF"
   residential → "#98FB98"
   other → "#00FFFF"

JSON format (return exactly this structure):
{"isNavigation":false,"center":[latitude,longitude],"buildingName":"Name","buildingType":"type","color":"#hex","description":"One sentence.","building":{"length":number,"width":number,"height":number}}

For navigation only:
{"isNavigation":true,"center":[latitude,longitude],"buildingName":"Place Name","description":"Navigating to Place Name."}`;

        // Auto-retry up to 3 times with delay for 503 overload errors
        let result, response, text;
        const models = ["gemini-2.5-flash", "gemini-2.0-flash-lite"];
        let lastError;

        for (let attempt = 1; attempt <= 3; attempt++) {
            try {
                const modelName = attempt === 3 ? models[1] : models[0];
                if (attempt > 1) {
                    console.log(`Retry attempt ${attempt} with model: ${modelName}...`);
                    await new Promise(r => setTimeout(r, attempt * 1500));
                }
                const model = genAI.getGenerativeModel({
                    model: modelName,
                    generationConfig: { responseMimeType: "application/json" }
                });
                result   = await model.generateContent(systemInstruction + "\nUser prompt: " + prompt);
                response = await result.response;
                text     = response.text().trim();
                lastError = null;
                break;
            } catch (err) {
                lastError = err;
                const is503 = err.message && (err.message.includes('503') || err.message.includes('high demand') || err.message.includes('overloaded'));
                if (!is503) throw err;
                console.warn(`503 overload on attempt ${attempt}:`, err.message);
            }
        }
        if (lastError) throw lastError;

        // Strip accidental markdown fences
        text = text.replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '').trim();

        // Extract JSON object
        const jsonMatch = text.match(/\{[\s\S]*\}/);
        if (!jsonMatch) {
            console.error("No JSON found in response:", text);
            throw new Error("AI did not return valid JSON");
        }

        const parsed = JSON.parse(jsonMatch[0]);

        if (!parsed.hasOwnProperty('isNavigation') || !parsed.center) {
            throw new Error("AI response missing required fields");
        }

        // Clamp to Malaysia bounds
        parsed.center[0] = Math.min(Math.max(parsed.center[0], 0.8), 7.4);
        parsed.center[1] = Math.min(Math.max(parsed.center[1], 99.5), 119.5);

        // Safety: never allow white/near-white colors for buildings (they vanish against OSM buildings)
        const nearWhiteColors = ['#F5F5F5','#FFFFFF','#FFF','#FFFAFA','#FEFEFE','#F0F0F0'];
        if (parsed.color && nearWhiteColors.includes(parsed.color.toUpperCase())) {
            parsed.color = '#00BFFF'; // fallback to deep sky blue
        }

        console.log(`✓ [${parsed.isNavigation ? 'NAV' : 'BUILD'}] ${parsed.buildingName ?? parsed.description}`);
        res.json(parsed);

    } catch (error) {
        console.error("AI Error:", error.message ?? error);
        res.status(500).json({ error: "Failed to process AI request: " + (error.message ?? "Unknown error") });
    }
});

const PORT = 3000;
app.listen(PORT, () => console.log(`System active at http://localhost:${PORT}`));