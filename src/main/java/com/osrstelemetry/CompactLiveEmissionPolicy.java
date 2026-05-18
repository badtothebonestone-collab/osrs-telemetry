package com.osrstelemetry;

final class CompactLiveEmissionPolicy
{
	private CompactLiveEmissionPolicy()
	{
	}

	static boolean snapshotNoFileLiveCacheOnly(
			boolean liveCacheEnabled,
			boolean compactPacketFilesEnabled,
			boolean compactStreamEnabled,
			boolean pluginSnapshotEndpointActive)
	{
		return liveCacheEnabled
				&& pluginSnapshotEndpointActive
				&& !compactPacketFilesEnabled
				&& !compactStreamEnabled;
	}

	static boolean navigationEffective(
			boolean emitNavigationConfigured,
			boolean navigationPacketTypeEnabled,
			boolean snapshotNoFileLiveCacheOnly)
	{
		return (emitNavigationConfigured && navigationPacketTypeEnabled) || snapshotNoFileLiveCacheOnly;
	}

	static boolean collisionWindowEffective(
			boolean emitNavigationConfigured,
			boolean emitCollisionWindowConfigured,
			boolean navigationPacketTypeEnabled,
			boolean collisionWindowPacketTypeEnabled,
			boolean snapshotNoFileLiveCacheOnly)
	{
		if (snapshotNoFileLiveCacheOnly)
		{
			return true;
		}
		return emitNavigationConfigured
				&& emitCollisionWindowConfigured
				&& navigationPacketTypeEnabled
				&& collisionWindowPacketTypeEnabled;
	}

	static boolean bankUiEffective(
			boolean bankUiPacketTypeEnabled,
			boolean snapshotNoFileLiveCacheOnly)
	{
		return bankUiPacketTypeEnabled || snapshotNoFileLiveCacheOnly;
	}
}
