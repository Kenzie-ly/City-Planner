import asyncio

# Global Semaphores
llm_semaphore = asyncio.Semaphore(5)
nominatim_semaphore = asyncio.Semaphore(1)
overpass_semaphore = asyncio.Semaphore(2)
