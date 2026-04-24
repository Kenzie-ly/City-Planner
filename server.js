const express = require('express');
const { GoogleGenerativeAI } = require("@google/generative-ai");
const path = require('path');
require('dotenv').config();

const app = express();
const genAI = new GoogleGenerativeAI(process.env.GEMINI_API_KEY);
const WORKFLOW_API_BASE = process.env.WORKFLOW_API_BASE || 'http://localhost:8000';
const HISTORY_API_BASE = process.env.HISTORY_API_BASE || 'http://localhost:8080';

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

async function proxyRequest(req, res, baseUrl, endpoint, method = 'POST') {
    try {
        const url = `${baseUrl}${endpoint}`;
        const options = {
            method: method,
            headers: { 'Content-Type': 'application/json' }
        };
        if (method !== 'GET') {
            options.body = JSON.stringify(req.body || {});
        }

        const upstream = await fetch(url, options);
        const text = await upstream.text();
        const contentType = upstream.headers.get('content-type') || 'application/json';
        res.status(upstream.status);
        res.set('Content-Type', contentType);
        res.send(text);
    } catch (error) {
        console.error(`Proxy error for ${endpoint}:`, error?.message || error);
        res.status(502).json({ error: `Failed to reach backend at ${baseUrl}${endpoint}` });
    }
}

app.post('/api/start', async (req, res) => proxyRequest(req, res, WORKFLOW_API_BASE, '/api/start'));
app.post('/api/chat', async (req, res) => proxyRequest(req, res, WORKFLOW_API_BASE, '/api/chat'));

app.get('/get_history', async (req, res) => proxyRequest(req, res, HISTORY_API_BASE, '/get_history', 'GET'));
app.post('/add_history', async (req, res) => proxyRequest(req, res, HISTORY_API_BASE, '/add_history'));
app.post('/delete_history', async (req, res) => proxyRequest(req, res, HISTORY_API_BASE, '/delete_history'));
app.post('/select_history', async (req, res) => proxyRequest(req, res, HISTORY_API_BASE, '/select_history'));

// app.post('/api/map-prompt', async (req, res) => {
//     try {
//         const { prompt } = req.body;

//         const systemInstruction = `You are a Malaysian Urban Planning AI Assistant.
//             You MUST return ONLY a raw JSON object. No markdown. No code blocks. No backticks. No explanation. No extra text before or after. Just the JSON object starting with { and ending with }.

//             RULES:
//             1. If user is navigating/flying/going/showing/zooming to a place with NO build request → "isNavigation": true
//             2. If user wants to BUILD or SIMULATE → "isNavigation": false, fill all fields
//             3. Coordinates must be within Malaysia: lat 0.8 to 7.4, lng 99.5 to 119.5
//             4. Extract building name from prompt. If none given, create a realistic Malaysian name.
//             5. Classify the building into ONE of these 6 categories and use the exact color:

//             COMMERCIAL (malls, retail stores, office towers, office areas, hotels, resorts, airports, restaurants, cafes, food courts, logistics hubs, warehouses, theme parks, mixed-use developments) → color "#E53935"
//             RESIDENTIAL (houses, apartments, condominiums, housing estates, kampung, residential areas) → color "#FF8C00"
//             INDUSTRIAL (factories, refineries, manufacturing plants, storage tanks, industrial parks) → color "#FFD700"
//             PUBLIC (hospitals, clinics, government buildings, LRT stations, MRT stations, KTM stations, bus terminals, schools, universities, civic centres, community halls) → color "#43A047"
//             RELIGIOUS (mosques, churches, temples, surau, shrine) → color "#00BCD4"
//             OTHER (anything that does not fit above) → color "#9575CD"

//             Return the category name as buildingType (e.g. "commercial", "residential", "industrial", "public", "religious", "other").

//             JSON format (return exactly this structure):
//             {"isNavigation":false,"center":[latitude,longitude],"buildingName":"Name","buildingType":"commercial|residential|industrial|public|religious|other","color":"#hex","description":"One sentence.","building":{"length":number,"width":number,"height":number}}

//             For navigation only:
//             {"isNavigation":true,"center":[latitude,longitude],"buildingName":"Place Name","description":"Navigating to Place Name."}`;

//         // Auto-retry up to 3 times with delay for 503 overload errors
//         let result, response, text;
//         const models = ["gemini-2.5-flash", "gemini-2.0-flash-lite"];
//         let lastError;

//         for (let attempt = 1; attempt <= 3; attempt++) {
//             try {
//                 const modelName = attempt === 3 ? models[1] : models[0];
//                 if (attempt > 1) {
//                     console.log(`Retry attempt ${attempt} with model: ${modelName}...`);
//                     await new Promise(r => setTimeout(r, attempt * 1500));
//                 }
//                 const model = genAI.getGenerativeModel({
//                     model: modelName,
//                     generationConfig: { responseMimeType: "application/json" }
//                 });
//                 result   = await model.generateContent(systemInstruction + "\nUser prompt: " + prompt);
//                 response = await result.response;
//                 text     = response.text().trim();
//                 lastError = null;
//                 break;
//             } catch (err) {
//                 lastError = err;
//                 const is503 = err.message && (err.message.includes('503') || err.message.includes('high demand') || err.message.includes('overloaded'));
//                 if (!is503) throw err;
//                 console.warn(`503 overload on attempt ${attempt}:`, err.message);
//             }
//         }
//         if (lastError) throw lastError;

//         // Strip accidental markdown fences
//         text = text.replace(/^```json\s*/i, '').replace(/^```\s*/i, '').replace(/\s*```$/i, '').trim();

//         // Extract JSON object
//         const jsonMatch = text.match(/\{[\s\S]*\}/);
//         if (!jsonMatch) {
//             console.error("No JSON found in response:", text);
//             throw new Error("AI did not return valid JSON");
//         }

//         const parsed = JSON.parse(jsonMatch[0]);

//         if (!parsed.hasOwnProperty('isNavigation') || !parsed.center) {
//             throw new Error("AI response missing required fields");
//         }

//         // Clamp to Malaysia bounds
//         parsed.center[0] = Math.min(Math.max(parsed.center[0], 0.8), 7.4);
//         parsed.center[1] = Math.min(Math.max(parsed.center[1], 99.5), 119.5);

//         // Enforce correct color per category (in case AI goes rogue)
//         const categoryColors = {
//             commercial:  '#E53935',
//             residential: '#FF8C00',
//             industrial:  '#FFD700',
//             public:      '#43A047',
//             religious:   '#00BCD4',
//             other:       '#9575CD'
//         };
//         if (parsed.buildingType && categoryColors[parsed.buildingType]) {
//             parsed.color = categoryColors[parsed.buildingType];
//         }
//         // Safety fallback for unknown/missing type
//         if (!parsed.color || parsed.color.toUpperCase() === '#FFFFFF' || parsed.color.toUpperCase() === '#F5F5F5') {
//             parsed.color = '#9575CD';
//         }

//         console.log(`✓ [${parsed.isNavigation ? 'NAV' : 'BUILD'}] ${parsed.buildingName ?? parsed.description}`);
//         res.json(parsed);

//     } catch (error) {
//         console.error("AI Error:", error.message ?? error);
//         res.status(500).json({ error: "Failed to process AI request: " + (error.message ?? "Unknown error") });
//     }
// });

const PORT = 3000;
app.listen(PORT, () => console.log(`System active at http://localhost:${PORT}`));
