"""
Sequence compression/decompression using Zstandard with dictionary compression.

This module mirrors the compression logic from Loculus backend's CompressionService.kt,
using reference genome sequences as dictionaries for improved compression ratios.
"""

import base64
from typing import Dict, Optional

import zstandard as zstd

from querulus.config import BackendConfig


class CompressionService:
    """Service for compressing and decompressing genetic sequences using Zstandard."""

    def __init__(self, config: BackendConfig):
        """
        Initialize compression service with reference genomes as dictionaries.

        Args:
            config: Backend configuration containing reference genomes for all organisms
        """
        self.config = config
        # Pre-compile dictionaries for each organism/segment combination
        self._nucleotide_dicts: Dict[tuple[str, str], Optional[zstd.ZstdCompressionDict]] = {}
        self._amino_acid_dicts: Dict[tuple[str, str], Optional[zstd.ZstdCompressionDict]] = {}

    def _get_nucleotide_dictionary(
        self, organism: str, segment_name: str
    ) -> Optional[zstd.ZstdCompressionDict]:
        """
        Get or create Zstd dictionary for a nucleotide sequence segment.

        Args:
            organism: Organism name (e.g., 'west-nile')
            segment_name: Segment name (e.g., 'main')

        Returns:
            Zstd dictionary or None if no reference genome defined
        """
        key = (organism, segment_name)
        if key not in self._nucleotide_dicts:
            organism_config = self.config.organisms.get(organism)
            if not organism_config:
                raise ValueError(f"Unknown organism: {organism}")

            reference_seq = organism_config.referenceGenome.get_nucleotide_sequence(
                segment_name
            )
            if reference_seq:
                # Create dictionary from reference genome sequence (UTF-8 bytes)
                dict_data = reference_seq.encode("utf-8")
                self._nucleotide_dicts[key] = zstd.ZstdCompressionDict(dict_data)
            else:
                self._nucleotide_dicts[key] = None

        return self._nucleotide_dicts[key]

    def _get_amino_acid_dictionary(
        self, organism: str, gene_name: str
    ) -> Optional[zstd.ZstdCompressionDict]:
        """
        Get or create Zstd dictionary for an amino acid sequence gene.

        Args:
            organism: Organism name (e.g., 'west-nile')
            gene_name: Gene name (e.g., 'E')

        Returns:
            Zstd dictionary or None if no reference genome defined
        """
        key = (organism, gene_name)
        if key not in self._amino_acid_dicts:
            organism_config = self.config.organisms.get(organism)
            if not organism_config:
                raise ValueError(f"Unknown organism: {organism}")

            reference_seq = organism_config.referenceGenome.get_gene_sequence(gene_name)
            if reference_seq:
                # Create dictionary from reference genome sequence (UTF-8 bytes)
                dict_data = reference_seq.encode("utf-8")
                self._amino_acid_dicts[key] = zstd.ZstdCompressionDict(dict_data)
            else:
                self._amino_acid_dicts[key] = None

        return self._amino_acid_dicts[key]

    def decompress_nucleotide_sequence(
        self, compressed_b64: str, organism: str, segment_name: str
    ) -> str:
        """
        Decompress a nucleotide sequence.

        Args:
            compressed_b64: Base64-encoded compressed sequence
            organism: Organism name
            segment_name: Segment name (e.g., 'main')

        Returns:
            Decompressed sequence string

        Raises:
            ValueError: If decompression fails
        """
        return self._decompress(
            compressed_b64,
            self._get_nucleotide_dictionary(organism, segment_name),
        )

    def decompress_amino_acid_sequence(
        self, compressed_b64: str, organism: str, gene_name: str
    ) -> str:
        """
        Decompress an amino acid sequence.

        Args:
            compressed_b64: Base64-encoded compressed sequence
            organism: Organism name
            gene_name: Gene name (e.g., 'E')

        Returns:
            Decompressed sequence string

        Raises:
            ValueError: If decompression fails
        """
        return self._decompress(
            compressed_b64,
            self._get_amino_acid_dictionary(organism, gene_name),
        )

    def _decompress(
        self, compressed_b64: str, dictionary: Optional[zstd.ZstdCompressionDict]
    ) -> str:
        """
        Decompress a Base64-encoded Zstd-compressed sequence.

        This mirrors the logic from CompressionService.kt:
        1. Base64 decode the compressed data
        2. Decompress using Zstd with optional dictionary
        3. Return UTF-8 string

        Args:
            compressed_b64: Base64-encoded compressed data
            dictionary: Optional Zstd dictionary for decompression

        Returns:
            Decompressed UTF-8 string

        Raises:
            ValueError: If decompression fails
        """
        try:
            # Step 1: Base64 decode
            compressed_bytes = base64.b64decode(compressed_b64)

            # Step 2: Decompress with Zstd
            if dictionary:
                dctx = zstd.ZstdDecompressor(dict_data=dictionary)
            else:
                dctx = zstd.ZstdDecompressor()

            decompressed_bytes = dctx.decompress(compressed_bytes)

            # Step 3: Decode UTF-8
            return decompressed_bytes.decode("utf-8")

        except Exception as e:
            raise ValueError(f"Failed to decompress sequence: {e}") from e
