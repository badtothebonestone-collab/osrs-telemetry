package com.osrstelemetry;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;

public class CompactLiveEmissionPolicyTest
{
	@Test
	public void snapshotNoFileLiveCacheOnlyRequiresCacheEndpointAndNoFileOrStream()
	{
		assertTrue(CompactLiveEmissionPolicy.snapshotNoFileLiveCacheOnly(true, false, false, true));
		assertFalse(CompactLiveEmissionPolicy.snapshotNoFileLiveCacheOnly(false, false, false, true));
		assertFalse(CompactLiveEmissionPolicy.snapshotNoFileLiveCacheOnly(true, true, false, true));
		assertFalse(CompactLiveEmissionPolicy.snapshotNoFileLiveCacheOnly(true, false, true, true));
		assertFalse(CompactLiveEmissionPolicy.snapshotNoFileLiveCacheOnly(true, false, false, false));
	}

	@Test
	public void snapshotNoFileForcesNavigationForLiveCacheWithoutEnablingFiles()
	{
		assertTrue(CompactLiveEmissionPolicy.navigationEffective(false, false, true));
		assertTrue(CompactLiveEmissionPolicy.collisionWindowEffective(false, false, false, false, true));
	}

	@Test
	public void normalCompactNavigationStillHonorsConfiguredPacketGates()
	{
		assertTrue(CompactLiveEmissionPolicy.navigationEffective(true, true, false));
		assertFalse(CompactLiveEmissionPolicy.navigationEffective(true, false, false));
		assertFalse(CompactLiveEmissionPolicy.navigationEffective(false, true, false));

		assertTrue(CompactLiveEmissionPolicy.collisionWindowEffective(true, true, true, true, false));
		assertFalse(CompactLiveEmissionPolicy.collisionWindowEffective(true, false, true, true, false));
		assertFalse(CompactLiveEmissionPolicy.collisionWindowEffective(true, true, true, false, false));
	}
}
