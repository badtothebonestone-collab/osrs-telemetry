package com.osrstelemetry;

public class LivePacket
{
	public static final String ENVELOPE_SCHEMA = "osrs_telemetry_live_packet.v1";

	public String schema;
	public String packetType;
	public String sessionId;
	public long tick;
	public long sequence;
	public String timestampUtc;
	public Object payload;

	public LivePacket(String packetType, String sessionId, long tick, long sequence, String timestampUtc, Object payload)
	{
		this.schema = ENVELOPE_SCHEMA;
		this.packetType = packetType;
		this.sessionId = sessionId;
		this.tick = tick;
		this.sequence = sequence;
		this.timestampUtc = timestampUtc;
		this.payload = payload;
	}
}
